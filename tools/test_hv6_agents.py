"""Tests for tools/hv6_agents.py. Synthetic cards and StubModel only: no API call, no network, no key, no tape, no HV4/Q50 output."""
from __future__ import annotations

import json
import re

import pytest

from tools import hv4_arms
from tools import hv4_prompts as p4
from tools import hv6_agents as a
from tools.haiku_caller import DEFAULT_MODEL, BadArguments
from tools.hv4_model import ModelReply
from tools.haiku_caller import Ledger  # noqa: F401
from tools.hv4_testkit import T0_MS, make_card, make_cards, reply, skip, watch

NOW = T0_MS + 2_000
DAY = "2026-11-02"
DAY_MS = 1_793_577_600_000          # 2026-11-02T00:00:00Z
assert a.b4.utc_day(DAY_MS) == DAY


def gbuy(pid, **over):
    d = {"id": pid, "action": "BUY", "size": 0.25, "conviction": 4,
         "plan": {"stop_pct": -25, "target_pct": 100, "trail_pct": 30, "time_min": 360},
         "why": "net_flow_60m is positive and ret_15m is a shallow dip"}
    d.update(over)
    return d


def ent(cards, text, *, knobs=None, received=NOW, stop_reason="end_turn", error=None):
    return a.validate_entry(ModelReply(text=text, stop_reason=stop_reason, error=error), cards=cards,
                            knobs=knobs or a.Knobs.v0(), received_ms=received)


# ------------------------------------------------------------------------------------------------ model id, arms, spec
def test_model_id_and_arm_table():
    assert a.MODEL_ID == "claude-haiku-5-5" == DEFAULT_MODEL
    assert set(a.ARMS) == {"L", "L-nr", "F", "P", "X"}
    assert a.llm_arms() == ("L", "L-nr", "F", "P")
    assert not a.ARMS["X"].llm and not a.ARMS["P"].promotable
    assert a.reviewer_arms() == ("L", "L-nr", "P") and a.regime_arms() == ("L", "P")


def test_x_has_no_model_ledger():
    for kind in a.KINDS:
        with pytest.raises(ValueError):
            a.ledger_name("X", kind)
    with pytest.raises(ValueError):
        a.ledger_name("F", "review")
    with pytest.raises(ValueError):
        a.ledger_name("L-nr", "regime")
    assert a.ledger_name("L", "entry") == a.ledger_name("L", "recheck") == "L"
    assert a.ledger_name("P", "review") == "REV-P" and a.ledger_name("L", "regime") == "RGM-L"


def test_spec_defaults_inside_bounds():
    for k, v in a.GEN_SPEC.plan_defaults.items():
        lo, hi = a.GEN_SPEC.plan_bounds[k]
        assert lo <= v <= hi
    assert a.GEN_SPEC.min_conviction_v0 == 3
    assert a.v0_knobs()["min_conviction"] == 3
    with pytest.raises(ValueError):
        a.Knobs(6)
    with pytest.raises(ValueError):
        a.Knobs(True)


# ------------------------------------------------------------------------------------------------ playbook
def test_playbook_v0_wording():
    pb = a.load_playbook()
    assert pb.name == "GEN" and pb.rules_words <= p4.RULES_WORDS_V0_MAX
    low = pb.text.lower()
    assert "default answer" not in low and "too eager" not in low          # #814 symmetric wording
    assert "BUY, WATCH or SKIP" in pb.text
    for k, (lo, hi) in a.GEN_SPEC.plan_bounds.items():
        assert f"{k} from {lo:g} to {hi:g}" in pb.text or f"{k} from {hi:g} to {lo:g}" in pb.text
    for title in ("NEUTRAL FIELDS", "MEASURED DIRECTIONS"):
        assert title in pb.rules
    with pytest.raises(p4.PromptError):
        a.load_playbook("../v0")


# ------------------------------------------------------------------------------------------------ requests
def test_entry_request_one_marker_model_and_limits():
    pb = a.load_playbook()
    cards = make_cards(3)
    req = a.build_entry_request("L", pb, cards, 0)
    body = req.body()
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 1200 == req.max_tokens  # fix A: HV6-only (HV4 entry is 400)
    assert json.dumps(body).count("cache_control") == 1
    assert len(req.system) == 1 and req.system[0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in body
    assert req.persona == "GEN" and req.ids == tuple(c["id"] for c in cards)
    with pytest.raises(p4.PromptError):
        a.build_entry_request("X", pb, cards, 0)
    with pytest.raises(p4.PromptError):
        a.build_entry_request("L", pb, make_cards(5), 0)
    with pytest.raises(p4.PromptError):
        a.build_entry_request("L", p4.playbook_from_text("CON", "v0", pb.text), cards, 0)


def test_cards_under_two_hours_or_unaged_are_refused():
    pb = a.load_playbook()
    for age in (1.99, 0.0, None, "3", True, float("nan")):
        with pytest.raises(BadArguments):          # PromptError is a BadArguments; NaN is refused earlier as not plain JSON
            a.build_entry_request("L", pb, [make_card(age_h=age)], 0)
    a.build_entry_request("L", pb, [make_card(age_h=2.0)], 0)
    c = make_card()
    del c["age_h"]
    with pytest.raises(p4.PromptError):
        a.build_entry_request("L", pb, [c], 0)


def test_arms_share_card_bytes_but_not_request_bytes():
    pb = a.load_playbook()
    cards = make_cards(2)
    reqs = {arm: a.build_entry_request(arm, pb, cards, 7) for arm in a.llm_arms()}
    assert len({r.batch_sha for r in reqs.values()}) == 1 and len({r.card_shas for r in reqs.values()}) == 1
    assert len({r.nonce for r in reqs.values()}) == 4
    assert len({r.request_sha256() for r in reqs.values()}) == 4          # a cached reply can never cross arms
    again = a.build_entry_request("L", pb, cards, 7)
    assert again.request_sha256() == reqs["L"].request_sha256()           # restart rebuilds the same bytes
    assert a.build_entry_request("L", pb, cards, 8).request_sha256() != reqs["L"].request_sha256()


def test_recheck_request():
    pb = a.load_playbook()
    cards = make_cards(2)
    pos = [{"id": c["id"], "stop_pct": -25, "target_pct": 100, "mark_ret_pct": 3.5, "as_of_ms": T0_MS} for c in cards]
    req = a.build_recheck_request("F", pb, pos, cards, 1)
    assert req.kind == "recheck" and req.max_tokens == 1200 == req.body()["max_tokens"] and json.dumps(req.body()).count("cache_control") == 1  # fix A
    with pytest.raises(p4.PromptError):
        a.build_recheck_request("F", pb, list(reversed(pos)), cards, 1)
    with pytest.raises(p4.PromptError):
        a.build_recheck_request("X", pb, pos, cards, 1)


# ------------------------------------------------------------------------------------------------ validators
def test_entry_actions_and_names_kept_apart():
    cards = make_cards(5)
    ids = [c["id"] for c in cards]
    items = [gbuy(ids[0]), skip(ids[1]), gbuy(ids[2], conviction=2), watch(ids[3]), gbuy(ids[4], plan={"stop_pct": -50, "target_pct": 100, "trail_pct": 30, "time_min": 360})]
    vs = ent(cards, reply(*items))
    assert [v.action for v in vs] == ["BUY", "SKIP", "BELOW_MIN", "WATCH", "PARSE_ERROR"]
    assert vs[2].fields["wanted"] == "BUY" and vs[4].reasons == ("bounds:stop_pct",)
    assert a.is_entry(vs[0]) and not any(a.is_entry(v) for v in vs[1:])
    t = a.tally(vs)
    assert t["by_action"]["BELOW_MIN"] == 1 and t["valid"] == 4 and t["wanted_act"] == 3 and t["parse_error"] == 1


def test_stale_reply_is_not_a_skip_and_not_an_entry():
    cards = make_cards(1)
    vs = ent(cards, reply(gbuy(cards[0]["id"])), received=T0_MS + 60_000)
    assert vs[0].action == "STALE" and vs[0].fields["wanted"] == "BUY" and not a.is_entry(vs[0])


def test_plan_needs_all_four_keys_and_trail_in_bounds():
    cards = make_cards(1)
    pid = cards[0]["id"]
    no_trail = gbuy(pid, plan={"stop_pct": -7, "target_pct": 12, "time_min": 45})
    assert ent(cards, reply(no_trail))[0].reasons == ("schema:plan_keys",)
    assert ent(cards, reply(gbuy(pid, plan={"stop_pct": -7, "target_pct": 12, "trail_pct": 30, "time_min": 45})))[0].action == "BUY"
    assert ent(cards, reply(gbuy(pid, plan={"stop_pct": -7, "target_pct": 200, "trail_pct": 30, "time_min": 45})))[0].reasons == ("bounds:target_pct",)


def test_refusal_truncation_garbage_and_errors_abstain():
    cards = make_cards(2)
    ids = [c["id"] for c in cards]
    good = reply(gbuy(ids[0]), skip(ids[1]))
    for kw, act, inval in (({"text": good, "stop_reason": "refusal"}, "PARSE_ERROR", True),
                           ({"text": good, "stop_reason": "max_tokens"}, "PARSE_ERROR", True),
                           ({"text": "not json"}, "PARSE_ERROR", True),
                           ({"text": ""}, "PARSE_ERROR", True),
                           ({"text": good, "error": "budget_exceeded"}, "NO_OUTPUT", False)):
        vs = ent(cards, kw["text"], stop_reason=kw.get("stop_reason", "end_turn"), error=kw.get("error"))
        assert [v.action for v in vs] == [act, act] and all(v.invalid is inval for v in vs)
        assert not any(a.is_entry(v) for v in vs)
    # an id that was not sent voids the whole reply
    assert ent(cards, reply(gbuy("Nope"), skip(ids[1])))[0].reasons == ("unknown_id",)


def test_why_trim_is_soft():
    cards = make_cards(1)
    long_why = "net_flow_60m ret_15m " + " ".join(["word"] * 24)           # 26 words: over 25, under 50
    v = ent(cards, reply(gbuy(cards[0]["id"], why=long_why)))[0]
    assert v.action == "BUY" and "why_trimmed" in v.reasons and not v.invalid


def test_recheck_defect_is_hold():
    pos = [{"id": "Pool00" + "1" * 38}]
    vs = a.validate_recheck(ModelReply(text="garbage", stop_reason="end_turn"), positions=pos, received_ms=NOW)
    assert vs[0].action == "HOLD" and vs[0].invalid
    ok = a.validate_recheck(ModelReply(text=json.dumps([{"id": pos[0]["id"], "action": "HOLD"}]), stop_reason="end_turn"), positions=pos, received_ms=NOW)
    assert ok[0].action == "HOLD" and not ok[0].invalid


def test_reject_max_size_zero_is_not_a_stake():
    from tools import hv5_validate as v5
    cards = [make_card("Pool00" + "1" * 38)]
    r = v5.validate_risk(ModelReply(text=json.dumps([{"id": cards[0]["id"], "grade": "reject", "flags": ["THIN_EXIT"], "why": "impact_025 is high and real_quote thin"}]),
                                    stop_reason="end_turn"), cards=cards)[0]
    assert r.action == "reject" and r.fields["max_size"] == 0.0
    assert a.model_written_size(r) is None
    caution = v5.validate_risk(ModelReply(text=json.dumps([{"id": cards[0]["id"], "grade": "pass", "flags": [], "max_size": 0.5, "why": "impact_025 low, real_quote deep"}]),
                                          stop_reason="end_turn"), cards=cards)[0]
    assert a.model_written_size(caution) == 0.5
    assert a.model_written_size(a.Verdict("x", "SKIP", {})) is None


# ------------------------------------------------------------------------------------------------ budget
def test_cap_constants():
    assert a.LLM_ARM_DAY_CAP_USD == 0.50 and a.GLOBAL_DAY_CAP_USD == 3.00
    assert a.day_cap_usd("L") == 0.50 and a.day_cap_usd("REV-P") == 0.05 and a.day_cap_usd("RGM-L") == 0.025
    assert a.caps_total_usd() == pytest.approx(2.20)
    for bad in ("X", "REV-F", "RGM-L-nr", "RISK", "REV-X"):
        with pytest.raises(ValueError):
            a.day_cap_usd(bad)
    with pytest.raises(ValueError):
        a.BudgetGuard("/tmp/x", global_cap_usd=3.5)


def test_stub_flow_and_per_arm_ledgers(tmp_path):
    g = a.BudgetGuard(tmp_path)
    m = a.StubModel(guard=g, cost_usd=0.01)
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS + 1000)
    r1 = m.complete(a.build_entry_request("L", pb, cards, 0), DAY_MS + 2000)
    r2 = m.complete(a.build_entry_request("F", pb, cards, 0), DAY_MS + 2000)
    assert r1.error is None and r2.error is None and m.model == "claude-haiku-5-5"
    assert g.spent("L", DAY) == pytest.approx(0.01) and g.spent("F", DAY) == pytest.approx(0.01) and g.spent("L-nr", DAY) == 0
    assert g.day_spent(DAY) == pytest.approx(0.02)
    assert a.validate_entry(r1, cards=cards, knobs=a.Knobs.v0(), received_ms=DAY_MS + 2500)[0].action == "SKIP"
    rep = g.day_report(DAY)
    assert rep["summary"]["day_spent_usd"] == pytest.approx(0.02) and not rep["summary"]["group_halted"]
    assert "ledgers" not in rep and "group" not in rep
    full = g.day_report(DAY, include_per_arm=True)
    assert full["ledgers"]["L"]["spent_usd"] == pytest.approx(0.01)


def test_arm_day_cap_halts_whole_group_and_x(tmp_path):
    g = a.BudgetGuard(tmp_path)
    m = a.StubModel(guard=g, cost_usd=0.1)
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS + 1000)
    got = [m.complete(a.build_entry_request("P", pb, cards, i), DAY_MS + 5000 + i) for i in range(6)]
    assert [r.error for r in got[:5]] == [None] * 5 and got[5].error in ("day_cap", "group_halted")
    # P spent 0.50: every member is halted, X in step; L has not spent a cent
    assert g.spent("L", DAY) == 0
    assert g.policy(DAY_MS + 9000).entries_halted and g.policy(DAY_MS + 9000).x_entries_halted and g.x_entries_halted(DAY_MS + 9000)
    assert m.complete(a.build_entry_request("L", pb, cards, 0), DAY_MS + 9000).error == "group_halted"
    assert m.complete(a.build_recheck_request("F", pb, [{"id": cards[0]["id"]}], cards, 0), DAY_MS + 9000).error == "group_halted"
    # reviewers are never dropped by a group step
    assert g.preflight("L", "review", DAY_MS + 9000).allowed
    # the halt lasts until 00:00Z only
    assert not g.policy(DAY_MS + 86_400_000 + 1).entries_halted


def test_degrade_ratchet_before_and_after_noon(tmp_path):
    g = a.BudgetGuard(tmp_path)
    m = a.StubModel(guard=g, cost_usd=0.1)
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS)
    for arm in ("L", "L-nr", "F"):                 # 0.6 of a 2.00 group budget = 30%: below the 60% trigger
        for i in range(2):
            m.complete(a.build_entry_request(arm, pb, cards, i), DAY_MS + 1000)
    p = g.policy(DAY_MS + 2000)
    assert p.level == 0 and p.board_enabled and p.max_cards_per_call == 4
    for arm in ("L", "L-nr", "F"):                 # +0.6 = 1.2 of 2.0 = 60%: step 1 before noon
        for i in range(2, 4):
            m.complete(a.build_entry_request(arm, pb, cards, i), DAY_MS + 3000)
    p = g.policy(DAY_MS + 4000)
    assert p.level == 1 and not p.board_enabled and p.max_cards_per_call == 2 and not p.entries_halted
    assert g.policy(DAY_MS + 13 * 3_600_000).level >= 1          # ratchet never falls within the day
    # the same spend after noon reads lower (80% trigger) but the mark persists
    assert g.persisted_level(DAY)[0] == 1


def test_global_cap_is_enforced_and_halts(tmp_path):
    g = a.BudgetGuard(tmp_path, global_cap_usd=0.30)
    m = a.StubModel(guard=g, cost_usd=0.09)
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS)
    errs = [m.complete(a.build_entry_request(arm, pb, cards, 0), DAY_MS + 1000 + i).error
            for i, arm in enumerate(("L", "L-nr", "F", "P"))]
    assert errs[:3] == [None] * 3 and errs[3] == "budget_exceeded"          # 0.27 spent, 0.03 left: a 0.09 call cannot fit
    assert g.day_spent(DAY) == pytest.approx(0.27)
    assert g.persisted_level(DAY) == (4, "global_day_cap")                  # the refusal halts the whole group
    assert g.x_entries_halted(DAY_MS + 5000)


def test_global_cap_denies_reviewers_too(tmp_path):
    g = a.BudgetGuard(tmp_path, global_cap_usd=0.30)
    led = Ledger(g.ledger_path("L", DAY))
    led.path.parent.mkdir(parents=True, exist_ok=True)
    with led.locked(exclusive=True):
        fd = led.open_append()
        try:
            Ledger.append_row(fd, {"cost_usd": 0.299, "ts_ms": DAY_MS, "source": "stub"})
        finally:
            import os
            os.close(fd)
    pf = g.preflight("L", "review", DAY_MS + 9000)
    assert not pf.allowed and pf.reason == "global_day_cap" and pf.global_limited
    assert g.persisted_level(DAY)[0] == 0                                    # a reviewer denial alone halts no trading group
    pf = g.preflight("F", "entry", DAY_MS + 9000)
    assert not pf.allowed and pf.reason == "global_day_cap" and g.persisted_level(DAY) == (4, "global_day_cap")


def test_month_guard_halts_all(tmp_path):
    g = a.BudgetGuard(tmp_path, month_limit_usd=0.25)
    m = a.StubModel(guard=g, cost_usd=0.1)
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS)
    m.complete(a.build_entry_request("L", pb, cards, 0), DAY_MS + 1000)
    m.complete(a.build_entry_request("L-nr", pb, cards, 0), DAY_MS + 1001)
    pf = g.preflight("F", "entry", DAY_MS + 2000)
    assert pf.allowed and pf.budget_usd == pytest.approx(0.05) and pf.month_limited      # headroom 0.05 is the effective budget
    m.complete(a.build_entry_request("F", pb, cards, 0), DAY_MS + 2000)
    pf = g.preflight("P", "entry", DAY_MS + 3000)
    assert not pf.allowed and pf.reason in ("month_guard", "group_halted")
    assert g.persisted_level(DAY)[0] == 4


def test_budget_exceeded_from_stub_records_refusal(tmp_path):
    g = a.BudgetGuard(tmp_path)
    m = a.StubModel(guard=g, cost_usd=0.3)                     # one call fits, a second 0.3 does not fit the 0.50 cap
    pb = a.load_playbook()
    cards = make_cards(1, as_of_ms=DAY_MS)
    assert m.complete(a.build_entry_request("L", pb, cards, 0), DAY_MS + 1000).error is None
    assert m.complete(a.build_entry_request("L", pb, cards, 1), DAY_MS + 1001).error == "budget_exceeded"
    assert g.persisted_level(DAY)[0] == 4


def test_damaged_ledger_fails_closed(tmp_path):
    g = a.BudgetGuard(tmp_path)
    p = g.ledger_path("L", DAY)
    p.parent.mkdir(parents=True)
    p.write_text("not json\n")
    assert g.preflight("F", "entry", DAY_MS + 1).reason == "ledger_unreadable"


def test_unreadable_state_reads_as_halted(tmp_path):
    g = a.BudgetGuard(tmp_path)
    sp = g._state_path(DAY)
    sp.parent.mkdir(parents=True)
    sp.write_text('{"level": 9}\n')
    assert g.persisted_level(DAY) == (4, "state_unreadable")


def test_x_cannot_be_preflighted(tmp_path):
    with pytest.raises(ValueError):
        a.BudgetGuard(tmp_path).preflight("X", "entry", DAY_MS)


def test_caller_model_real_path_is_never_called(tmp_path):
    calls = []

    class Boom:
        def __init__(self, *args, **kw):
            calls.append(("init", kw.get("save_dir")))

        def call(self, **kw):
            raise AssertionError("tests must not reach the API")

    g = a.BudgetGuard(tmp_path)
    m = a.CallerModel(g, caller_cls=Boom)
    assert m.model == "claude-haiku-5-5"
    g.halt_group(DAY_MS, "test")
    cards = make_cards(1, as_of_ms=DAY_MS)
    assert m.complete(a.build_entry_request("L", a.load_playbook(), cards, 0), DAY_MS + 5).error == "group_halted"
    assert calls == []


# ------------------------------------------------------------------------------------------------ regime_rule
KNOBS = a.v0_knobs()
RG = "RG-0a1b2c3d"
BLOCK = {"schema": "hv6_regime.v1", "radar_status": "ok", "as_of_ms": NOW - 1000, "events": [{"id": RG}, {"id": "RG-ffffffff"}]}
UNI = {"ret_15m": 1.0, "net_flow_15m": 0.5}


def rr(**over):
    d = {"type": "regime_rule", "target": "stop_pct", "new": -20, "ttl_h": 48, "evidence_ids": [RG], "why": "direct HIGH event on pump program"}
    d.update(over)
    return d


def vr(change, **kw):
    kw.setdefault("knobs", KNOBS)
    kw.setdefault("block", BLOCK)
    kw.setdefault("now_ms", NOW)
    kw.setdefault("universe", UNI)
    return a.validate_regime_rule(change, **kw)


def test_tightening_allowed_per_knob():
    cases = [("stop_pct", -20), ("time_min", 120)]
    for target, new in cases:
        v = vr(rr(target=target, new=new))
        assert v.ok and v.mode == "tighten" and v.tag is None and v.match_rule_waived and v.change["new"] == new, (target, v)
        assert v.expires_ms == NOW + 48 * 3_600_000


def test_target_trail_conviction_tightening_refused():
    for target, new in (("target_pct", 60), ("trail_pct", 25), ("min_conviction", 4)):
        v = vr(rr(target=target, new=new))
        assert not v.ok and v.reason == "tightening_not_allowed_for_knob", target
    assert a.TIGHTENING_TARGETS == ("stop_pct", "time_min")


def test_no_change_and_wrong_direction_without_probe_keys():
    assert vr(rr(new=-25)).reason == "no_change"
    loose = vr(rr(target="stop_pct", new=-28))               # looser, no probe keys
    assert not loose.ok and loose.reason == "loosen_needs_probe_keys"
    assert vr(rr(new=-20, stake=0.25, extra_entries_per_h=0)).reason == "tighten_has_probe_keys"


def test_cites_a_present_rg_id():
    assert vr(rr(evidence_ids=["RG-11111111"])).reason == "unknown_evidence_id"
    assert vr(rr(evidence_ids=[])).reason == "bad_evidence_ids"
    assert vr(rr(evidence_ids=["x"])).reason == "bad_evidence_ids"
    assert vr(rr(evidence_ids=[RG, RG])).reason == "unknown_evidence_id"
    assert vr(rr(evidence_ids=[RG, "RG-ffffffff"])).ok


def test_block_must_be_usable_and_before_the_write():
    assert vr(rr(), block=None).reason == "block_unusable"
    for st in ("absent", "stale", "bogus"):
        assert vr(rr(), block={**BLOCK, "radar_status": st}).reason == "block_unusable"
    assert vr(rr(), block={**BLOCK, "radar_status": "partial"}).ok
    assert vr(rr(), block={**BLOCK, "as_of_ms": NOW}).reason == "block_not_before_write"
    assert vr(rr(), block={**BLOCK, "as_of_ms": None}).reason == "block_not_before_write"


def test_ttl_bounds():
    for ttl, ok in ((1, True), (72, True), (73, False), (0, False), (-1, False), (True, False), (1.5, False), ("48", False)):
        assert vr(rr(ttl_h=ttl)).ok is ok, ttl
    c = rr()
    del c["ttl_h"]
    assert vr(c).reason == "bad_keys"


def test_bounds_and_types():
    assert vr(rr(new=-31)).reason == "knob_out_of_bounds"
    assert vr(rr(target="stop_pct", new=-3)).reason == "knob_out_of_bounds"
    assert vr(rr(target="min_conviction", new=6)).reason == "knob_out_of_bounds"
    assert vr(rr(target="time_min", new=120.5)).reason == "knob_not_integer"
    assert vr(rr(new="-20")).reason == "knob_not_number"
    assert vr(rr(new=True)).reason == "knob_not_number"
    assert vr(rr(new=float("nan"))).reason == "knob_not_number"
    assert vr(rr(target="nope")).reason == "unknown_target"
    assert vr(rr(), knobs={}).reason == "knob_missing"


def test_forbidden_targets_and_size():
    for t in ("scanner", "hard_stop_pct", "max_open", "fills", "costs", "scorer", "gate", "model", "reviewer_prompt", "embargo", "controls", "risk_facts", "vetoes"):
        assert vr(rr(target=t, new=1)).reason == "forbidden_target", t
    assert vr(rr(target="size", new=0.25)).reason == "size_is_not_a_knob"
    assert vr(rr(target="stake", new=0.5)).reason == "size_is_not_a_knob"


def test_probe_rules():
    probe = rr(target="stop_pct", new=-30, stake=0.25, extra_entries_per_h=1)          # -25 -> -30 is exactly one 5-pt step, in bounds
    v = vr(probe)
    assert v.ok and v.mode == "probe" and v.tag == "regime_probe" and v.change["stake"] == 0.25 and v.change["extra_entries_per_h"] == 1
    assert vr({**probe, "stake": 0.5}).reason == "probe_stake_not_0.25"
    assert vr({**probe, "stake": None}).reason == "probe_stake_not_0.25"
    assert vr({**probe, "extra_entries_per_h": 2}).reason == "probe_extra_entries"
    assert vr({**probe, "extra_entries_per_h": True}).reason == "probe_extra_entries"
    k = {**KNOBS, "stop_pct": -10.0}
    assert vr(rr(target="stop_pct", new=-16, stake=0.25, extra_entries_per_h=0), knobs=k).reason == "probe_more_than_one_step"
    assert vr(rr(target="stop_pct", new=-15, stake=0.25, extra_entries_per_h=0), knobs=k).ok
    assert vr(rr(target="min_conviction", new=3, stake=0.25, extra_entries_per_h=0), knobs={**KNOBS, "min_conviction": 4}).mode == "probe"
    # a probe past a bound is refused by the bound, not by the step
    assert vr(rr(target="min_conviction", new=2, stake=0.25, extra_entries_per_h=0), knobs={**KNOBS, "min_conviction": 3}).reason == "knob_out_of_bounds"
    assert vr({**probe, "bogus": 1}).reason == "bad_keys"


def test_local_veto():
    v = vr(rr(target="local_veto", new="ret_15m < -3"))
    assert v.ok and v.mode == "tighten" and v.change["new"] == "ret_15m < -3"
    assert vr(rr(target="local_veto", new="ret_15m < -3"), vetoes=("ret_15m < -3",)).reason == "duplicate_veto"
    assert vr(rr(target="local_veto", new="ret_15m < -3"), vetoes=tuple(f"ret_15m < {-i - 4}" for i in range(8))).reason == "veto_cap"
    assert vr(rr(target="local_veto", new="not a predicate")).ok is False
    assert vr(rr(target="local_veto", new="unknown_field > 1")).ok is False
    assert vr(rr(target="local_veto", new="top10_frac > 0.3"), universe={"top10_frac": 0.3}).reason == "neutral_field"
    assert vr(rr(target="local_veto", new="ret_15m < -3"), universe=None).reason == "no_universe"
    assert vr(rr(target="local_veto", new="ret_15m < -3", stake=0.25, extra_entries_per_h=0)).reason == "veto_cannot_be_a_probe"


def test_why_and_shape():
    assert vr(rr(why="x" * 500)).reason == "bad_why"
    assert vr(rr(why="")).reason == "bad_why"
    assert vr(rr(why="a b " * 30)).reason == "bad_why"
    assert vr(rr(why='quote " inside')).reason == "bad_why"
    assert vr(rr(type="playbook_edit")).reason == "bad_type"
    assert vr("nope").reason == "not_object"
    assert vr(rr(target=5)).reason == "bad_target"


def test_list_limits():
    ch = [rr(target="stop_pct", new=-20), rr(target="time_min", new=120), rr(target="local_veto", new="ret_15m < -3")]
    assert [x.ok for x in a.validate_regime_rules(ch, knobs=KNOBS, block=BLOCK, now_ms=NOW, universe=UNI)] == [True] * 3
    four = ch + [rr(target="time_min", new=100)]
    assert [x.reason for x in a.validate_regime_rules(four, knobs=KNOBS, block=BLOCK, now_ms=NOW, universe=UNI)] == ["too_many_changes"] * 4
    dup = [rr(new=-20), rr(new=-15)]
    assert [x.ok for x in a.validate_regime_rules(dup, knobs=KNOBS, block=BLOCK, now_ms=NOW, universe=UNI)] == [True, False]
    assert a.validate_regime_rules("x", knobs=KNOBS, block=BLOCK, now_ms=NOW, universe=UNI)[0].reason == "not_a_list"


def test_no_secret_or_hv4_output_paths_in_module():
    src = open(a.__file__, encoding="utf-8").read()
    assert not re.search(r"ANTHROPIC_API_KEY|sk-ant|/data/mal/arena/haiku-v4|/var/lib/mal/anthropic|getenv", src)
    assert str(a.DEFAULT_ROOT) == "/data/mal/arena/haiku-v6"


# ------------------------------------------------------------------------------------------------ PR 917 changes (reviewer + QP)
def test_x_exit_is_pinned_and_separate_from_skip_pricing():
    assert a.X_EXIT == {"stop_pct": -8.0, "target_pct": 20.0, "trail_pct": None, "time_min": 60}
    assert a.X_ENTRIES_PER_H == 2 and a.x_exit() == a.X_EXIT and a.x_exit() is not a.X_EXIT
    assert a.SKIP_PRICING_PLAN == {"stop_pct": -25.0, "target_pct": 100.0, "trail_pct": 30.0, "time_min": 360}
    assert a.X_EXIT["trail_pct"] is None and a.X_EXIT["target_pct"] < a.GEN_SPEC.code_limits["trail_arms_after_pct"]
    lo, hi = a.GEN_SPEC.plan_bounds["stop_pct"]
    assert lo <= a.X_EXIT["stop_pct"] <= hi and a.GEN_SPEC.plan_bounds["time_min"][0] <= a.X_EXIT["time_min"]


def _damage(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n")


def test_ledger_failure_halts_group_and_x_failclosed_reviewer_ledger(tmp_path):
    g = a.BudgetGuard(tmp_path)
    _damage(g.ledger_path("REV-L", DAY))
    assert g.preflight("F", "entry", DAY_MS + 10).reason == "ledger_unreadable"
    assert g.persisted_level(DAY)[0] == 4 and g.persisted_level(DAY)[1] == "ledger_unreadable"
    assert g.x_entries_halted(DAY_MS + 20) is True


def test_ledger_failure_earlier_day_of_month(tmp_path):
    g = a.BudgetGuard(tmp_path)
    _damage(g.ledger_path("L", "2026-11-01"))
    assert g.x_entries_halted(DAY_MS + 20) is True            # month_spent reads it; no preflight happened first
    assert g.persisted_level(DAY) == (4, "ledger_unreadable")
    assert g.preflight("L-nr", "entry", DAY_MS + 30).reason in ("ledger_unreadable", "group_halted")


def test_ledger_failure_today_x_does_not_raise(tmp_path):
    g = a.BudgetGuard(tmp_path)
    _damage(g.ledger_path("L", DAY))
    assert g.x_entries_halted(DAY_MS + 20) is True
    assert g.persisted_level(DAY) == (4, "ledger_unreadable")


def test_x_halted_false_when_healthy(tmp_path):
    assert a.BudgetGuard(tmp_path).x_entries_halted(DAY_MS + 20) is False


def test_regime_probe_extra_entries_are_a_total():
    k = {**KNOBS, "stop_pct": -10.0, "time_min": 120}
    p1 = rr(target="stop_pct", new=-15, stake=0.25, extra_entries_per_h=1)
    p2 = rr(target="time_min", new=150, stake=0.25, extra_entries_per_h=1)
    p3 = rr(target="target_pct", new=110, stake=0.25, extra_entries_per_h=0)
    out = a.validate_regime_rules([p1, p2, p3], knobs=k, block=BLOCK, now_ms=NOW)
    assert [x.ok for x in out] == [True, False, True] and out[1].reason == "extra_entries_over_total"
    # already-active probes count; an expired one does not
    live = [{"extra_entries_per_h": 1, "expires_ms": NOW + 1000}]
    assert [x.reason for x in a.validate_regime_rules([p1], knobs=k, block=BLOCK, now_ms=NOW, active_probes=live)] == ["extra_entries_over_total"]
    dead = [{"extra_entries_per_h": 1, "expires_ms": NOW}]
    assert a.validate_regime_rules([p1], knobs=k, block=BLOCK, now_ms=NOW, active_probes=dead)[0].ok


def test_runner_helper_caps_extra_entries_across_active_probes():
    live = [{"extra_entries_per_h": 1, "expires_ms": NOW + 5}]
    assert a.active_extra_entries(live, NOW) == 1 and a.active_extra_entries(live, NOW + 5) == 0
    assert not a.probe_extra_entries_allowed(live, 1, NOW) and a.probe_extra_entries_allowed(live, 0, NOW)
    assert a.probe_extra_entries_allowed([], 1, NOW) and not a.probe_extra_entries_allowed([], 2, NOW)
    assert not a.probe_extra_entries_allowed([], True, NOW) and not a.probe_extra_entries_allowed([], -1, NOW)
    junk = [{"extra_entries_per_h": "1", "expires_ms": NOW + 5}, {}]
    assert a.active_extra_entries(junk, NOW) == 0


@pytest.mark.parametrize("bad", ["/data/mal/arena/haiku-v4", "/x/hv4/y", "/x/Q50", "/x/shadow-1", "/x/canary", "/x/forward-1016",
                                 "/data/mal/fresh-08", "/var/lib/mal/sealed", "/x/meta-capture", "/x/bots.json"])
def test_guard_refuses_denied_roots(bad):
    with pytest.raises(ValueError):
        a.BudgetGuard(bad)


def test_guard_accepts_hv6_root():
    a.BudgetGuard(a.DEFAULT_ROOT)


def test_regime_max_tokens_and_unhashable_event_id():
    assert a.MAX_TOKENS["regime"] == 1200 and a.MAX_TOKENS["entry"] == 1200 and a.MAX_TOKENS["recheck"] == 1200 and a.MAX_TOKENS["review"] == 4000
    assert "regime" not in hv4_arms.MAX_TOKENS                                    # HV4's table is untouched
    for eid in ([], {}, None, 5, ["RG-0a1b2c3d"]):
        blk = {**BLOCK, "events": [{"id": eid}, {"id": RG}]}
        assert vr(rr(), block=blk).ok
        assert vr(rr(evidence_ids=[RG]), block={**BLOCK, "events": [{"id": eid}]}).reason == "unknown_evidence_id"
    assert vr(rr(), block={**BLOCK, "events": "RG-0a1b2c3d"}).reason == "unknown_evidence_id"
    assert vr(rr(), block={**BLOCK, "events": None}).reason == "unknown_evidence_id"


# ------------------------------------------------------------------------------------------------ fix A (smoke #1806): HV6-only max_tokens
def test_fix_a_hv6_only_max_tokens_and_hv4_table_untouched():
    from tools import hv6_cards as h6
    assert hv4_arms.MAX_TOKENS == {"entry": 400, "risk": 400, "recheck": 120, "review": 4000}       # HV4's frozen table is not edited
    assert a.TOKENS_PER_ITEM_MAX == 300 and a.ITEMS_PER_CALL_MAX == h6.CARDS_PER_CALL == a.ENTRY_MAX_CARDS == 4
    assert a.MAX_TOKENS["entry"] == a.MAX_TOKENS["recheck"] == a.TOKENS_PER_ITEM_MAX * h6.CARDS_PER_CALL == 1200
    # the largest item of the 62 complete smoke-#1806 replies was 165 output tokens (structure-only read): four of them fit with room
    assert 4 * 165 < a.MAX_TOKENS["entry"]
    pb = a.load_playbook()
    for n in (1, 4):
        req = a.build_entry_request("P", pb, make_cards(n), 0)
        assert req.max_tokens == 1200 and req.body()["max_tokens"] == 1200
    # the request bytes change with the limit (so request_sha256 changes; disclosed in the PR): same cards, HV4's 400 gives another sha
    req = a.build_entry_request("L", pb, make_cards(2), 0)
    import dataclasses as _dc
    assert _dc.replace(req, max_tokens=400).request_sha256() != req.request_sha256()


def test_fix_a_reviewer_and_regime_limits_unchanged_in_bundle_spec():
    from tools import hv6_release as rel
    assert a.MAX_TOKENS["review"] == 4000 and a.MAX_TOKENS["regime"] == 1200
    assert rel.bundle_spec_sha256() == "82848deb4a534ac501604f77c06deb7fdd9e2c85a2915627f91675955c07fd4c"   # the pack's pin: fix A does not move it
    assert rel.regime_spec_sha256() == "50be8a3f2e088c64511aeb1fb127277d9e0438e3b5ce44e46c5381338eb2c414"


# ------------------------------------------------------------------------------------------------ fix B: in-flight holds (concurrent arm calls)
def _charge(g, ledger, usd):
    import os
    led = Ledger(g.ledger_path(ledger, DAY))
    led.path.parent.mkdir(parents=True, exist_ok=True)
    with led.locked(exclusive=True):
        fd = led.open_append()
        try:
            Ledger.append_row(fd, {"cost_usd": usd, "ts_ms": DAY_MS, "source": "stub"})
        finally:
            os.close(fd)


def test_fix_b_inflight_hold_is_taken_off_other_calls_headroom(tmp_path):
    import threading
    g = a.BudgetGuard(tmp_path, global_cap_usd=0.30)
    _charge(g, "L", 0.25)
    base = g.preflight("F", "entry", DAY_MS + 1000)
    assert base.allowed and base.budget_usd == pytest.approx(0.05) and g.others_inflight_usd() == 0.0   # no call in flight: the pre-fix-B arithmetic
    entered, release = threading.Event(), threading.Event()

    def other():
        with g.inflight(0.02):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=other)
    t.start()
    assert entered.wait(5)
    try:
        assert g.others_inflight_usd() == pytest.approx(0.02)
        pf = g.preflight("F", "entry", DAY_MS + 2000)
        assert pf.allowed and pf.budget_usd == pytest.approx(0.03) and pf.global_limited      # the other call's hold is off the global headroom
        with g.inflight(0.01):                                                                 # this thread's own hold is never taken off its own preflight
            assert g.others_inflight_usd() == pytest.approx(0.02)
            assert g.preflight("F", "entry", DAY_MS + 2000).budget_usd == pytest.approx(0.03)
    finally:
        release.set()
        t.join()
    assert g.others_inflight_usd() == 0.0 and g.preflight("F", "entry", DAY_MS + 3000).budget_usd == pytest.approx(0.05)


def test_fix_b_two_holds_that_do_not_both_fit_deny_the_second(tmp_path):
    import threading
    g = a.BudgetGuard(tmp_path, global_cap_usd=0.30)
    _charge(g, "L", 0.30 - 0.004)                      # 0.004 of global headroom left
    entered, release = threading.Event(), threading.Event()

    def other():
        with g.inflight(0.0035):                       # a call in flight elsewhere holds most of it
            entered.set()
            release.wait(5)

    t = threading.Thread(target=other)
    t.start()
    assert entered.wait(5)
    try:
        pf = g.preflight("L", "review", DAY_MS + 2000)  # a reviewer kind: a denial here halts no trading group
        assert not pf.allowed and pf.reason == "global_day_cap" and pf.global_limited
    finally:
        release.set()
        t.join()
    assert g.preflight("L", "review", DAY_MS + 3000).allowed      # the same call fits once the other one has finished


def test_fix_b_models_hold_the_worst_case_while_the_call_runs(tmp_path):
    import threading
    g = a.BudgetGuard(tmp_path)
    req = a.build_entry_request("L", a.load_playbook(), make_cards(4, as_of_ms=DAY_MS), 0)
    worst = a.call_worst_case_usd(req)
    assert 0 < worst < 0.05
    seen = []
    in_call, go = threading.Event(), threading.Event()

    def slow(r):
        in_call.set()
        go.wait(5)
        return A_SKIPS(r)

    m = a.StubModel(slow, guard=g)
    th = threading.Thread(target=lambda: seen.append(m.complete(req, DAY_MS + 1000)))
    th.start()
    assert in_call.wait(5)
    try:
        assert g.others_inflight_usd() == pytest.approx(worst)        # visible to every other thread while the call is in flight
    finally:
        go.set()
        th.join()
    assert seen[0].error is None and g.others_inflight_usd() == 0.0
    with pytest.raises(ValueError):
        with g.inflight(float("nan")):
            pass


def A_SKIPS(req):
    return json.dumps([{"id": i, "action": "SKIP", "why": "x"} for i in req.ids])
