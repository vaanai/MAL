"""Tests for tools/hv6_review.py. SYNTHETIC ledgers, cards and a hand-made regime block; StubModel only. No API call, no network, no key, no tape, and no
/data/mal/arena, HV4 or Q50 output is read (seals: hunt30/SEALS-BRIEF.md). Nothing here says the trader makes money or that a reviewer learns anything."""
from __future__ import annotations

import inspect
import io
import json
import os
import stat
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tools import hv4_review as RV
from tools import hv6_agents as ag
from tools import hv6_cards as HC
from tools import hv6_review as R
from tools.hv4_model import ModelReply
from tools.hv4_testkit import make_card

ORIG_READ, ORIG_WRITE = HC.READ_ROOTS, HC.WRITE_ROOTS
TOOLS = Path(__file__).resolve().parent
DAY = "2026-11-02"
CUT = R.day_start_ms(DAY)
H = 3_600_000
T0 = CUT - 5 * 86_400_000
NOW = CUT + 5 * 60_000
COMMIT = "a" * 40
BLOCK = {"schema": "hv6_regime.v1", "radar_status": "ok", "as_of_ms": CUT - 3 * H,
         "events": [{"id": "RG-0123abcd", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade", "program": "pump"},
                    {"id": "RG-89abcdef", "sev": "LOW", "relevance": "adjacent", "kind": "new_vocab", "program": "OTHER"}]}


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """The guard's allowlist is the test's tmp dir; the wall clock is a fixed instant before every effective_from (R6 append rule)."""
    real = os.path.realpath(tmp_path)
    monkeypatch.setattr(HC, "READ_ROOTS", (real,))
    monkeypatch.setattr(HC, "WRITE_ROOTS", (real,))
    clk = [T0 + 1]
    monkeypatch.setattr(R, "wall_ms", lambda: clk[0])
    orig_call = R._call

    def _call(model, req, now_ms):                      # the model call happens "now": the wall clock is the instant the test passed in
        clk[0] = now_ms
        return orig_call(model, req, now_ms)

    monkeypatch.setattr(R, "_call", _call)


def blk(i=0):
    """A block with its own direct HIGH event id (each intraday review needs a NEW one)."""
    ev = {"id": f"RG-{i:08x}", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade", "program": "pump"}
    return {**BLOCK, "events": [ev, *BLOCK["events"][1:]]}


def rec(i, *, kind="trade", net=1.0, ret=0.0, reason="target", hold=300, close=None, entry=None):
    close = CUT - H - i * 1000 if close is None else close
    c = make_card(f"Pool{i:04d}" + "1" * 34, close - hold * 1000, ret_15m=ret)
    if kind == "trade":
        dec = {"conviction": 4, "size": 0.25, "plan": {"stop_pct": -25, "target_pct": 100, "trail_pct": 30, "time_min": 360}, "why": "ret_15m is shallow"}
        rid, ent = f"t{i:04d}", close - hold * 1000 - RV.EXIT_FILL_MS
    else:
        dec = {"action": "SKIP", "reasons": ["model_skip"]}
        rid, ent = f"s{i:04d}", CUT - 86_400_000 + 1000 * i
    return {"id": rid, "kind": kind, "entry_ms": ent if entry is None else entry, "close_ms": close, "card": c, "decision": dec, "exit_reason": reason,
            "hold_s": hold, "net_pct": net}


def history(n=40):
    """First half dip trades that lose, second half that win (a planted split)."""
    return [rec(i, ret=-10.0 if i < n // 2 else 2.0, net=-3.0 if i < n // 2 else 1.0, reason="stop" if i < n // 2 else "target", hold=100 + i) for i in range(n)]


KNOB = {"type": "knob", "target": "min_conviction", "new": 4, "predicate": "ret_15m < -5", "claim": "worse", "evidence_ids": ["t0000"], "why": "dips lose"}
REGIME = {"type": "regime_rule", "target": "stop_pct", "new": -10, "ttl_h": 24, "evidence_ids": ["RG-0123abcd"], "why": "pump program redeploy"}


def state():
    return R.v0_state()


def setup(tmp_path, arm="L"):
    row, _ = R.genesis_manifest(arm, T0, COMMIT)
    R.append_manifest(tmp_path, arm, row, None, None)
    return row


def stub(text="[]", **kw):
    return ag.StubModel(lambda req: ModelReply(text=text, **kw) if kw else text)


# ------------------------------------------------------------------------------------------------ params, knobs
def test_params_registry():
    assert R.V60_PARAMS.min_matches == 10 and R.V60_PARAMS.max_changes == 3
    for k, bad in (("min_matches", 9), ("skip_count", 61), ("max_changes", 4), ("ttl_h_max", 96), ("evidence_days", 6)):
        with pytest.raises(RV.ReviewError):
            R.Params(**{k: bad})
    assert R.Params(min_matches=15).sha256() != R.V60_PARAMS.sha256()


def test_gen_knob_bounds():
    assert R.check_knob("stop_pct", -30) is None and R.check_knob("stop_pct", -31) == "knob_out_of_bounds"
    assert R.check_knob("min_conviction", 3) is None and R.check_knob("min_conviction", 2) == "knob_out_of_bounds"
    assert R.check_knob("size", 1) == "knob_unknown" and R.check_knob("time_min", 30.5) == "knob_not_integer"
    assert R.check_knob("trail_pct", True) == "knob_not_number"


def test_v0_playbook_passes_paragraph_rules():
    pb = ag.load_playbook("v0")
    assert RV.check_playbook_paragraph_rules(pb, pb) == []


# ------------------------------------------------------------------------------------------------ bundle: G4, cutoff, block, size
def test_bundle_t0_cutoff_and_perturbation():
    recs = history() + [rec(900, entry=T0 - 1)]                       # a pre-T0 entry never enters (G4)
    b = R.build_bundle("L", DAY, state(), recs, t0_ms=T0)
    assert "t0900" not in b.text and len(b.obj["trades"]) == 40
    late = rec(901, close=CUT)                                         # closes exactly at the cutoff: out
    later = rec(902, close=CUT + H)
    assert R.build_bundle("L", DAY, state(), recs + [late, later], t0_ms=T0).sha256 == b.sha256


def test_bundle_never_shows_true_close_or_entry():
    b = R.build_bundle("L", DAY, state(), history(), t0_ms=T0)
    for it in b.obj["trades"]:
        assert set(it) == {"id", "card", "decision", "exit_reason", "hold_s", "net_pct"}
    assert json.loads(b.audit_text)["closes"]["t0000"] == CUT - H


def test_regime_evidence_only_for_l_and_p():
    assert "regime_evidence" in R.build_bundle("L", DAY, state(), history(), t0_ms=T0, block=BLOCK).obj
    assert "regime_evidence" in R.build_bundle("P", DAY, state(), history(), t0_ms=T0, block=BLOCK).obj
    nr = R.build_bundle("L-nr", DAY, state(), history(), t0_ms=T0, block=BLOCK)
    assert "regime_evidence" not in nr.obj and nr.block_sha256 is None
    with pytest.raises(RV.ReviewError):
        R.build_bundle("F", DAY, state(), history(), t0_ms=T0)


def test_bundle_size_truncation_and_skip_count():
    recs = history() + [rec(500 + i, kind="skip", net=-1.0) for i in range(30)]
    full = R.build_bundle("L", DAY, state(), recs, t0_ms=T0)
    assert len(full.obj["skips"]) == 30
    assert len(R.build_bundle("L", DAY, state(), recs, t0_ms=T0, params=R.Params(skip_count=20)).obj["skips"]) == 20
    small = R.build_bundle("L", DAY, state(), recs, t0_ms=T0, limit=full.request_bytes - 20_000)
    assert small.request_bytes <= full.request_bytes - 20_000 and small.dropped["candles_stripped"]
    with pytest.raises(RV.BundleTooLarge):
        R.build_bundle("L", DAY, state(), recs, t0_ms=T0, limit=1_000)


def test_request_shape_and_tokens():
    b = R.build_bundle("L", DAY, state(), history(), t0_ms=T0)
    assert b.request.kind == "review" and b.request.max_tokens == ag.MAX_TOKENS["review"] == 4000
    assert "own start" in b.request.system[0]["text"] and "cache_control" not in b.request.system[0]


# ------------------------------------------------------------------------------------------------ placebo
def test_placebo_is_deterministic_and_keeps_marginals():
    recs = history()
    p1 = R.build_bundle("P", DAY, state(), recs, t0_ms=T0)
    p2 = R.build_bundle("P", DAY, state(), recs, t0_ms=T0)
    assert p1.sha256 == p2.sha256
    key = lambda items: sorted((x["net_pct"], x["exit_reason"], x["hold_s"]) for x in items)
    l = R.build_bundle("L", DAY, state(), recs, t0_ms=T0)
    assert key(p1.obj["trades"]) == key(l.obj["trades"])              # same marginals
    assert [x["net_pct"] for x in p1.obj["trades"]] != [x["net_pct"] for x in l.obj["trades"]]   # link cut
    assert [x["id"] for x in p1.obj["trades"]] == [x["id"] for x in l.obj["trades"]]


def test_placebo_seed_is_sha256_arm_date():
    recs = RV.before_cutoff(history(), CUT)
    assert RV.shuffle_outcomes(recs, "P", DAY) == RV.shuffle_outcomes(recs, "P", DAY)
    assert RV.shuffle_outcomes(recs, "P", DAY) != RV.shuffle_outcomes(recs, "P", "2026-11-03")
    assert RV.sha256_hex(f"P|{DAY}") == "91bf3daa12a95a3c937c9e64d870841a3022de02b59a1bfcc39ab9f5597eafb3"
    out = RV.shuffle_outcomes(recs, "P", DAY)           # pinned: a change to the shuffle is a change to a pre-registered procedure
    assert RV.sha256_hex(RV.canon([[x["id"], x["net_pct"], x["hold_s"]] for x in out])) == "81a3f15f83ecf7e696582a1d1ccfa4335128a97881868d981fc74c97177d5b82"


# ------------------------------------------------------------------------------------------------ validator
def validate(text, arm="L", params=R.V60_PARAMS, block=BLOCK, recs=None, active=()):
    ev = RV.before_cutoff(recs or history(), CUT)
    return R.validate_reply(text, state(), ev, params=params, arm=arm, block=block, now_ms=NOW, active_rules=active)


def test_accepts_one_good_knob_change():
    r = validate(json.dumps([KNOB]))
    assert len(r.accepted) == 1 and r.state.knobs["min_conviction"] == 4 and not r.rejected


def test_more_than_three_changes_refuses_whole_reply():
    assert validate(json.dumps([KNOB] * 4)).reply_error == "too_many_changes"
    assert validate(json.dumps([KNOB, KNOB]), params=R.Params(max_changes=1)).reply_error == "too_many_changes"


def test_ten_match_rule_and_param():
    few = history(40)
    assert validate(json.dumps([{**KNOB, "predicate": "ret_15m < -9.99 AND hold_s < 0"}])).rejected  # junk predicate rejected
    r = validate(json.dumps([KNOB]), recs=[rec(i, ret=-10.0, net=-3.0) for i in range(9)] + [rec(50 + i, ret=2.0, net=1.0) for i in range(20)])
    assert r.rejected == ((0, "fewer_than_10_matches"),)
    r8 = validate(json.dumps([KNOB]), params=R.Params(min_matches=8), recs=[rec(i, ret=-10.0, net=-3.0) for i in range(9)] + [rec(50 + i, ret=2.0, net=1.0) for i in range(20)])
    assert len(r8.accepted) == 1
    assert validate(json.dumps([KNOB]), params=R.Params(min_matches=15), recs=few[:14] + few[30:]).rejected      # 7 dip matches in 24 records


def test_sign_and_evidence_rules():
    assert validate(json.dumps([{**KNOB, "claim": "better"}])).rejected == ((0, "effect_sign_mismatch"),)
    assert validate(json.dumps([{**KNOB, "evidence_ids": ["nope"]}])).rejected == ((0, "unknown_evidence_id"),)
    assert validate(json.dumps([{**KNOB, "evidence_ids": []}])).rejected == ((0, "bad_evidence_ids"),)
    assert validate(json.dumps([{**KNOB, "new": 9}])).rejected == ((0, "knob_out_of_bounds"),)
    assert validate(json.dumps([{**KNOB, "predicate": "top10_frac > 0.1"}])).rejected[0][1] == "neutral_field"


def test_regime_rule_for_l_p_not_l_nr():
    ok = validate(json.dumps([REGIME]))
    assert len(ok.regime_rules) == 1 and ok.regime_rules[0]["expires_ms"] == NOW + 24 * H and ok.regime_rules[0]["mode"] == "tighten"
    assert len(validate(json.dumps([REGIME]), arm="P").regime_rules) == 1
    assert validate(json.dumps([REGIME]), arm="L-nr", block=None).rejected == ((0, "regime_rule_not_available"),)
    assert validate(json.dumps([{**REGIME, "evidence_ids": ["RG-ffffffff"]}])).rejected == ((0, "unknown_evidence_id"),)
    assert validate(json.dumps([REGIME]), block={**BLOCK, "as_of_ms": NOW}).rejected == ((0, "block_not_before_write"),)
    assert validate(json.dumps([{**REGIME, "ttl_h": 48}]), params=R.Params(ttl_h_max=24)).rejected == ((0, "ttl_over_release_cap"),)


def test_regime_rule_waives_match_rule_and_counts_in_the_three():
    r = validate(json.dumps([REGIME, KNOB, {**KNOB, "target": "time_min", "new": 120}]))
    assert len(r.accepted) == 3 and len(r.regime_rules) == 1
    assert validate(json.dumps([REGIME, REGIME, KNOB, KNOB])).reply_error == "too_many_changes"


# ------------------------------------------------------------------------------------------------ manifest chain and nights
def test_genesis_and_chain_verify(tmp_path):
    setup(tmp_path)
    assert len(R.verify_chain(tmp_path, "L", reviewer_sha=R.reviewer_sha256(), frozen_sha=COMMIT)) == 1
    with pytest.raises(RV.ManifestRefused):
        R.verify_chain(tmp_path, "L", frozen_sha="b" * 40)
    with pytest.raises(RV.ReviewError):
        R.genesis_manifest("F", T0, COMMIT)


def test_night_accepts_change_and_chains(tmp_path):
    setup(tmp_path)
    row = R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([KNOB])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert row["kind"] == "nightly" and row["version"] == 1 and len(row["changes"]) == 1 and row["knobs"]["min_conviction"] == 4
    assert row["max_input_close_ms"] < CUT and row["block_as_of_ms"] < row["written_ms"]
    assert row["effective_from_ms"] == max(CUT + RV.EFFECTIVE_OFFSET_MS, NOW + R.APPEND_MARGIN_MS) and row["trigger_ids"] == []
    rows = R.verify_chain(tmp_path, "L", reviewer_sha=R.reviewer_sha256(), frozen_sha=COMMIT)
    assert rows[1]["prev_manifest_sha256"] == rows[0]["sha256"]
    again = R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + 1, commit=COMMIT, records=history())
    assert again["sha256"] == row["sha256"]
    av = R.active_version(tmp_path, "L", row["effective_from_ms"])
    assert av.state.knobs["min_conviction"] == 4 and R.active_version(tmp_path, "L", T0 + 1).manifest["version"] == 0


def test_night_before_cutoff_refused_and_tamper_detected(tmp_path):
    setup(tmp_path)
    with pytest.raises(RV.ReviewError):
        R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(), now_ms=CUT - 1, commit=COMMIT, records=history())
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([KNOB])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    (tmp_path / "playbooks" / "L" / "v1.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(RV.ManifestRefused):
        R.verify_chain(tmp_path, "L")


@pytest.mark.parametrize("kw", [{"stop_reason": "max_tokens"}, {"error": "RateLimited"}, {"stop_reason": "refusal"}])
def test_truncated_refused_or_failed_reply_is_an_abstain(tmp_path, kw):
    setup(tmp_path)
    row = R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([KNOB]), **kw), now_ms=NOW, commit=COMMIT, records=history())
    assert row["changes"] == [] and row["review_error"] and row["knobs"]["min_conviction"] == 3


def test_l_nr_chain_has_no_regime_fields(tmp_path):
    setup(tmp_path, "L-nr")
    row = R.review_night("L-nr", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([REGIME, KNOB])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert row["block_sha256"] is None and row["regime_sha256"] is None and row["regime_rules"] == []
    assert [c["type"] for c in row["changes"]] == ["knob"] and row["rejected"] == [[0, "regime_rule_not_available"]]


def test_night_with_regime_rule_and_expiry(tmp_path):
    setup(tmp_path)
    row = R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([REGIME])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert len(row["regime_rules"]) == 1 and row["knobs"] == R.v0_state().knobs
    assert R.active_version(tmp_path, "L", row["effective_from_ms"]).regime_rules
    assert R.active_version(tmp_path, "L", NOW + 25 * H).regime_rules == ()                       # expired
    d2 = "2026-11-03"
    c2 = R.day_start_ms(d2)
    recs2 = history() + [rec(700, close=c2 - H)]
    row2 = R.review_night("L", d2, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=c2 + 5 * 60_000, commit=COMMIT, records=recs2, block=BLOCK)
    assert row2["regime_rules"] == []                                                              # the rule had expired by then (24 h)


# ------------------------------------------------------------------------------------------------ intraday regime review
def test_regime_review_intraday_line(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    seen = []
    m = ag.StubModel(lambda req: (seen.append(req), json.dumps([REGIME]))[1])
    t = CUT + 8 * H
    row = R.regime_review("L", DAY, root=tmp_path, ledger_root=tmp_path, model=m, now_ms=t, commit=COMMIT, block=BLOCK, records=history())
    req = seen[0]
    assert req.kind == "regime" and req.max_tokens == ag.MAX_TOKENS["regime"] == 1200
    body = json.loads(req.user_text[req.user_text.index("{"):req.user_text.rindex("}") + 1])
    assert "trades" not in body and "skips" not in body and "regime_evidence" in body
    assert row["trigger_ids"] == ["RG-0123abcd"] and row["kind"] == "intraday" and row["intraday_n"] == 1 and row["effective_from_ms"] == t + RV.EFFECTIVE_OFFSET_MS
    assert row["block_as_of_ms"] < row["written_ms"] and row["max_input_close_ms"] < CUT
    assert row["playbook_sha256"] and len(row["regime_rules"]) == 1 and row["version"] == 2
    R.verify_chain(tmp_path, "L", reviewer_sha=R.reviewer_sha256(), regime_sha=R.regime_template_sha256(), frozen_sha=COMMIT)


def test_regime_review_caps_and_guards(tmp_path):
    setup(tmp_path)
    kw = dict(root=tmp_path, ledger_root=tmp_path, commit=COMMIT, records=history())
    # a missed nightly line does NOT block the day's intraday review; it is logged
    R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW, block=blk(1), **kw)
    ev = [json.loads(x) for x in (tmp_path / "logs" / "review-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ev[0]["kind"] == "nightly_missing_for_intraday"
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + 1, commit=COMMIT, records=history(), block=BLOCK)
    R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + H, block=blk(2), **kw)
    with pytest.raises(RV.ReviewError, match="intraday_cap"):
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + 2 * H, block=blk(3), **kw)
    with pytest.raises(RV.ReviewError, match="block_not_before_write"):
        R.regime_review("P", DAY, model=stub("[]"), now_ms=NOW, **{**kw, "block": {**blk(4), "as_of_ms": NOW + 1}})
    with pytest.raises(RV.ReviewError, match="no_regime_review"):
        R.regime_review("L-nr", DAY, model=stub("[]"), now_ms=NOW, block=blk(5), **kw)


def test_intraday_only_on_a_new_direct_high_event(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    kw = dict(root=tmp_path, ledger_root=tmp_path, commit=COMMIT, records=history())
    no_direct = {**BLOCK, "events": BLOCK["events"][1:]}
    with pytest.raises(RV.ReviewError, match="no_new_direct_high"):
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + H, block=no_direct, **kw)
    with pytest.raises(RV.ReviewError, match="no_new_direct_high"):
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + H, block={**BLOCK, "radar_status": "stale"}, **kw)
    R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + H, block=BLOCK, **kw)
    with pytest.raises(RV.ReviewError, match="no_new_direct_high"):                    # the same event id again
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + 2 * H, block=BLOCK, **kw)
    rows = R.read_manifests(tmp_path, "L")
    again = {**rows[-1], "seq": rows[-1]["seq"] + 1, "version": rows[-1]["version"] + 1, "intraday_n": 2, "prev_manifest_sha256": rows[-1]["sha256"],
             "written_ms": NOW + 2 * H, "effective_from_ms": NOW + 2 * H + RV.EFFECTIVE_OFFSET_MS}
    again["sha256"] = R.manifest_hash(again)
    with pytest.raises(RV.ManifestRefused, match="intraday_trigger"):
        R.check_manifest(again, rows, "L")


def test_regime_review_truncated_is_abstain_and_only_regime(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    kw = dict(root=tmp_path, ledger_root=tmp_path, commit=COMMIT, block=blk(1), records=history())
    row = R.regime_review("L", DAY, model=stub(json.dumps([REGIME]), stop_reason="max_tokens"), now_ms=NOW + H, **{**kw, "block": blk(1)})
    assert row["review_error"] == "truncated" and row["changes"] == [] and row["regime_rules"] == []
    row2 = R.regime_review("L", DAY, model=stub(json.dumps([KNOB])), now_ms=NOW + 2 * H, **{**kw, "block": blk(2)})
    assert row2["changes"] == [] and row2["rejected"] == [[0, "not_a_regime_rule"]] and row2["knobs"] == row["knobs"]


def test_direct_high_trigger_and_latest_block_x(tmp_path):
    assert R.direct_high_ids(BLOCK) == ["RG-0123abcd"]
    assert R.new_direct_high(BLOCK, ["RG-0123abcd"]) == []
    assert R.direct_high_ids({**BLOCK, "radar_status": "stale"}) == [] and R.direct_high_ids(None) == []
    for ms in (100, 200, 300):
        (tmp_path / f"block-{ms}.json").write_text(json.dumps({**BLOCK, "as_of_ms": ms}), encoding="utf-8")
    assert R.latest_block(tmp_path, 300)["as_of_ms"] == 200 and R.latest_block(tmp_path, 100) is None


def test_check_manifest_negatives(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    rows = R.read_manifests(tmp_path, "L")

    def bad(row, hist, code, **over):
        r = {**row, **over}
        r["sha256"] = R.manifest_hash(r)
        with pytest.raises(RV.ManifestRefused, match=code):
            R.check_manifest(r, hist, "L")

    night = rows[1]
    bad(night, rows[:1], "input_after_cutoff", max_input_close_ms=CUT)
    bad(night, rows[:1], "block_not_before_write", block_as_of_ms=night["written_ms"])
    bad(night, rows[:1], "params_changed", params_sha256="0" * 64)
    bad(night, rows[:1], "intraday_n", intraday_n=1)
    bad(night, rows[:1], "regime_rule_expired_or_malformed", regime_rules=[{"expires_ms": 1}], changes=[])
    intra = R.regime_review("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + H, commit=COMMIT, block=BLOCK, records=history())
    bad(intra, rows, "intraday_trigger", trigger_ids=[])
    bad(night, rows[:1], "trigger_ids", trigger_ids=["RG-0123abcd"])
    bad(intra, rows, "written_backdated", written_ms=night["written_ms"] - 1)
    bad(night, rows[:1], "effective_from", effective_from_ms=night["written_ms"] + R.APPEND_MARGIN_MS - 1 if night["written_ms"] + R.APPEND_MARGIN_MS > CUT + RV.EFFECTIVE_OFFSET_MS else CUT)
    bad(intra, rows, "intraday_changed_permanent_state", knobs={**intra["knobs"], "stop_pct": -20.0}, knobs_sha256=RV.sha256_hex(RV.canon({**intra["knobs"], "stop_pct": -20.0})))
    bad(intra, rows, "effective_from", effective_from_ms=intra["written_ms"])
    bad(intra, rows, "intraday_without_block", block_sha256=None, block_as_of_ms=None)


# ------------------------------------------------------------------------------------------------ loaders (synthetic files)
def _write_world(tmp, t0):
    root, led = tmp / "hv6root", tmp / "ledger"
    (root / "decisions" / "L").mkdir(parents=True)
    (led / "hv6.0-L").mkdir(parents=True)

    def pair(pid, entry, exit_, net):
        e = {"kind": "entry", "pos": True, "order_id": pid, "ms": entry, "size": 0.25, "conviction": 4, "plan": {"stop_pct": -25}, "why": "ok", "card": make_card(pid, entry)}
        t = {"kind": "trade", "position_id": pid, "stake_sol": 0.25, "decision_ms": {"entry": entry, "exit": exit_}, "exit_reason": "target",
             "legs": {"total": {"flat_1p9": {"net_lamports": int(net * 0.25e9)}}}}
        return e, t
    rows = [pair("A" * 40, t0 + 1000, t0 + 400_000, 0.1), pair("B" * 40, t0 - 5000, t0 + 400_000, 0.1), pair("C" * 40, CUT - 1000, CUT + 1000, 0.1)]
    (root / "decisions" / "L" / "2026-11-01.jsonl").write_text("\n".join(json.dumps(e) for e, _t in rows) + "\n", encoding="utf-8")
    (led / "hv6.0-L" / "trades-1.jsonl").write_text("\n".join(json.dumps(t) for _e, t in rows) + "\n", encoding="utf-8")
    return root, led


def test_loader_g4_t0_filter_and_cutoff(tmp_path):
    root, led = _write_world(tmp_path, T0)
    trades, open_n = R.load_trade_records("L", root, led, CUT, T0)
    assert [r["id"] for r in trades] == ["A" * 40]                    # B entered before T0; C closes after the cutoff
    assert trades[0]["net_pct"] == pytest.approx(10.0) and trades[0]["hold_s"] == 399
    assert open_n == 2                                                  # A's exit has no sell row here; C is open at the cutoff; B is pre-T0


def test_roots_with_deny_tokens_symlinks_and_outside_allowlist_are_refused(tmp_path):
    (tmp_path / "hv4real").mkdir()
    (tmp_path / "innocent").symlink_to(tmp_path / "hv4real")               # the raw string is clean, the real path is not
    for bad in ("/data/mal/arena/haiku-v4", str(tmp_path / "q50"), str(tmp_path / "bots.json"), str(tmp_path / "innocent"), "/etc", "relative/x"):
        with pytest.raises(RV.ReviewError, match="path_refused"):
            R.load_trade_records("L", bad, tmp_path, CUT, T0)
        with pytest.raises(RV.ReviewError, match="path_refused"):
            R.verify_chain(bad, "L")
    with pytest.raises(RV.ReviewError, match="path_refused"):                 # the shared engine ledger is not on the allowlist
        R.load_trade_records("L", tmp_path, "/data/mal/arena/ledger", CUT, T0)


def test_symlinked_file_and_ledger_dir_are_refused(tmp_path):
    root, led = _write_world(tmp_path, T0)
    (tmp_path / "hv4-elsewhere").mkdir()
    (tmp_path / "hv4-elsewhere" / "trades-1.jsonl").write_text("{}\n", encoding="utf-8")
    (led / "hv6.1-L").symlink_to(tmp_path / "hv4-elsewhere")
    with pytest.raises(RV.ReviewError, match="path_refused"):
        R.load_trade_records("L", root, led, CUT, T0, "v6.1")
    (root / "decisions" / "L" / "2026-11-01.jsonl").rename(tmp_path / "moved.jsonl")
    (root / "decisions" / "L" / "2026-11-01.jsonl").symlink_to(tmp_path / "hv4-elsewhere" / "trades-1.jsonl")
    with pytest.raises(RV.ReviewError, match="path_refused"):
        R.load_trade_records("L", root, led, CUT, T0)


def test_guard_allows_the_defaults_on_the_real_allowlist(monkeypatch):
    monkeypatch.setattr(HC, "READ_ROOTS", ORIG_READ)
    monkeypatch.setattr(HC, "WRITE_ROOTS", ORIG_WRITE)
    led = R.default_ledger_root(R.HV6_ROOT)
    assert led == "/data/mal/arena/haiku-v6/engine-ledger" and R._guard(led) == led
    assert R.default_ledger_root("/data/mal/arena/haiku-v6/releases/v6.1") == led
    with pytest.raises(RV.ReviewError):
        R._guard("/data/mal/arena/ledger")


def test_bot_id_is_one_function_and_loader_has_no_prefix_param():
    assert R.bot_id("v6.0", "L") == "hv6.0-L" and R.bot_id("v6.1", "L-nr") == "hv6.1-L-nr"
    for bad in ("6.0", "v7.0", "hv6.0", ""):
        with pytest.raises(RV.ReviewError):
            R.bot_id(bad, "L")
    assert "bot_prefix" not in inspect.signature(R.load_trade_records).parameters
    src = inspect.getsource(R)
    assert src.count('f"hv6.{') == 1 and 'hv6-{' not in src and 'hv6-L' not in src                    # the only place a bot id is built


def test_loader_opens_only_the_release_bot_dir(tmp_path):
    root, led = _write_world(tmp_path, T0)
    assert len(R.load_trade_records("L", root, led, CUT, T0, "v6.0")[0]) == 1
    (led / "hv6.1-L").mkdir()                                                # v6.1 has its own (empty) dir: v6.0's rows are not joined
    assert R.load_trade_records("L", root, led, CUT, T0, "v6.1")[0] == []


def test_missing_ledger_dir_with_sell_rows_is_a_refusal_not_an_empty_book(tmp_path):
    root, led = _write_world(tmp_path, T0)
    with (root / "decisions" / "L" / "2026-11-01.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "sell", "pid": "A" * 40, "ms": T0 + 400_000, "reason": "target"}) + "\n")
    assert R.load_trade_records("L", root, led, CUT, T0)[0]
    with pytest.raises(RV.ReviewError, match="ledger_dir_missing"):
        R.load_trade_records("L", root, tmp_path / "nowhere", CUT, T0)
    assert R.load_trade_records("L", tmp_path / "emptyroot", tmp_path / "nowhere", CUT, T0) == ([], 0)    # no decision rows: nothing to refuse


# ------------------------------------------------------------------------------------------------ block files
def _put_block(d, ms, **over):
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"block-{ms}.json"
    f.write_text(json.dumps({**BLOCK, "as_of_ms": ms, **over}), encoding="utf-8")
    return f


def test_read_block_file_checks(tmp_path):
    d = tmp_path / "regime"
    good = _put_block(d, 100)
    blk0, raw = R.read_block_file(good, root=tmp_path)
    assert blk0["as_of_ms"] == 100 and raw == good.read_bytes()
    for name, over, code in (("drill", {"drill": True}, "block_is_drill_or_synthetic"), ("syn", {"synthetic": 1}, "block_is_drill_or_synthetic"),
                             ("schema", {"schema": "other"}, "block_schema")):
        f = _put_block(tmp_path / name / "regime", 200, **over)
        with pytest.raises(RV.ReviewError, match=code):
            R.read_block_file(f)
    renamed = d / "block-999.json"
    renamed.write_text(json.dumps({**BLOCK, "as_of_ms": 100}), encoding="utf-8")                 # name does not carry the block's as_of_ms
    with pytest.raises(RV.ReviewError, match="block_filename"):
        R.read_block_file(renamed)
    elsewhere = _put_block(tmp_path / "other", 300)
    with pytest.raises(RV.ReviewError, match="block_not_under_regime_dir"):
        R.read_block_file(elsewhere, root=tmp_path)
    link = d / "block-400.json"
    link.symlink_to(elsewhere)
    with pytest.raises(RV.ReviewError):
        R.read_block_file(link, root=tmp_path)


def test_block_radar_byte_copy_must_match(tmp_path):
    d = tmp_path / "regime"
    copy = b'{"x":1}\n'
    f = _put_block(d, 500, input={"sha256": RV.sha256_hex(copy)})
    with pytest.raises(RV.ReviewError, match="radar_copy_mismatch"):
        R.read_block_file(f)
    (d / "radar-500.jsonl").write_bytes(copy)
    assert R.read_block_file(f)[0]["as_of_ms"] == 500
    (d / "radar-500.jsonl").write_bytes(copy + b" ")
    with pytest.raises(RV.ReviewError, match="radar_copy_mismatch"):
        R.read_block_file(f)


def test_latest_block_skips_invalid_files_and_night_stores_a_byte_copy(tmp_path):
    d = tmp_path / "regime"
    _put_block(d, CUT - 2 * H)
    _put_block(d, CUT - H, drill=True)                                      # newer but a drill: skipped
    assert R.latest_block(d, NOW)["as_of_ms"] == CUT - 2 * H
    setup(tmp_path)
    row = R.review_night("L", DAY, root=tmp_path, ledger_root=None, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history())
    assert row["block_as_of_ms"] == CUT - 2 * H
    assert (tmp_path / "bundles" / "L" / f"{DAY}.block.json").read_bytes() == (d / f"block-{CUT - 2 * H}.json").read_bytes()


def mark(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / R.DRILL_MARKER).write_text("", encoding="utf-8")


def test_cli_regime_block_must_come_from_the_regime_dir(tmp_path, capsys):
    mark(tmp_path)
    setup(tmp_path)
    out = _put_block(tmp_path / "elsewhere", CUT - H)
    rc = R.main(["regime", "--root", str(tmp_path), "--arm", "L", "--day", DAY, "--commit", COMMIT, "--now-ms", str(NOW), "--stub", "--block", str(out)])
    assert rc == 1 and "block_not_under_regime_dir" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ T0, R6
def test_genesis_clock_must_be_a_utc_midnight():
    for bad in (0, -86_400_000, T0 + 1, 12345, True, None):
        with pytest.raises(RV.ReviewError, match="clock_not_a_utc_midnight"):
            R.genesis_manifest("L", bad, COMMIT)
    row, _ = R.genesis_manifest("L", T0, COMMIT)
    for forged in (0, T0 + 1):
        r = {**row, "effective_from_ms": forged, "written_ms": forged}
        r["sha256"] = R.manifest_hash(r)
        with pytest.raises(RV.ManifestRefused, match="clock_not_a_utc_midnight"):
            R.check_manifest(r, [], "L")


def test_cli_genesis_has_no_clock_default(tmp_path):
    with pytest.raises(SystemExit):
        R.main(["genesis", "--root", str(tmp_path), "--arm", "L", "--commit", COMMIT])
    assert R.main(["genesis", "--root", str(tmp_path), "--arm", "L", "--commit", COMMIT, "--clock-start-ms", "0"]) == 1


def test_append_after_effective_from_is_refused(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(R, "wall_ms", lambda: CUT + 10 * H)               # the model call was so slow the line is appended after it is in force
    with pytest.raises(RV.ManifestRefused, match="appended_after_effective"):
        R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert len(R.read_manifests(tmp_path, "L")) == 1 and not (tmp_path / "replies").exists()
    monkeypatch.setattr(R, "wall_ms", lambda: NOW + 1)
    row = R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert row["effective_from_ms"] > NOW + 1


def test_now_cannot_backdate(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + H, commit=COMMIT, records=history(), block=BLOCK)
    with pytest.raises(RV.ReviewError, match="now_backdated"):
        R.regime_review("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, block=BLOCK, records=history())
    d2 = "2026-11-03"
    c2 = R.day_start_ms(d2)
    R.regime_review("L", d2, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=c2 + H, commit=COMMIT, block=blk(7), records=history())
    with pytest.raises(RV.ReviewError, match="now_backdated"):
        R.review_night("L", d2, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=c2 + 5 * 60_000, commit=COMMIT, records=history())


def test_future_dated_line_is_refused(tmp_path, monkeypatch):
    setup(tmp_path, "P")
    monkeypatch.setattr(R, "wall_ms", lambda: NOW - 2 * H)                     # the wall clock is 2 h behind the instant the caller claims
    monkeypatch.setattr(R, "_call", lambda m, req, now: ("[]", None, None))
    with pytest.raises(RV.ManifestRefused, match="written_in_future"):
        R.review_night("P", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert len(R.read_manifests(tmp_path, "P")) == 1                            # nothing written: later real lines are not silenced
    monkeypatch.setattr(R, "wall_ms", lambda: NOW - 30_000)                     # inside the 60 s slack
    assert R.review_night("P", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)["kind"] == "nightly"


def test_stub_and_now_ms_refused_on_a_production_chain(tmp_path, monkeypatch, capsys):
    prod = tmp_path / "haiku-v6"
    monkeypatch.setattr(R, "HV6_ROOT", str(prod))
    for root in (prod, prod / "releases" / "v6.1", tmp_path / "unmarked"):
        setup(root, "L-nr")
        base = ["run", "--root", str(root), "--arm", "L-nr", "--day", DAY, "--commit", COMMIT]
        for extra in (["--stub"], ["--now-ms", str(NOW)], ["--stub", "--now-ms", str(NOW)]):
            assert R.main(base + extra) == 1 and "stub_or_now_only_in_drill_roots" in capsys.readouterr().err
        assert len(R.read_manifests(root, "L-nr")) == 1
    mark(prod)                                                                   # a marker inside the production tree does not count
    assert R.main(["run", "--root", str(prod), "--arm", "L-nr", "--day", DAY, "--commit", COMMIT, "--stub"]) == 1
    assert not R.is_drill_root(prod) and not R.is_drill_root(prod / "releases" / "v6.1")


def test_drill_roots_allow_stub_and_now_ms(tmp_path, monkeypatch, capsys):
    prod = tmp_path / "haiku-v6"
    monkeypatch.setattr(R, "HV6_ROOT", str(prod))
    drill = prod / "drill" / "d1"
    marked = tmp_path / "fixture"
    mark(marked)
    for root in (drill, marked):
        assert R.is_drill_root(root)
        setup(root, "L-nr")
        assert R.main(["run", "--root", str(root), "--arm", "L-nr", "--day", DAY, "--commit", COMMIT, "--now-ms", str(NOW), "--stub"]) == 0
        assert len(R.read_manifests(root, "L-nr")) == 2
        capsys.readouterr()


# ------------------------------------------------------------------------------------------------ glue and perms
def test_make_regime_review_fn_adapts_the_runner_hook(tmp_path):
    setup(tmp_path)
    m = ag.StubModel(lambda req: json.dumps([REGIME]))
    fn = R.make_regime_review_fn(root=tmp_path, ledger_root=None, model=m, commit=COMMIT)
    assert fn("L", None, NOW) is None                                      # an arm without a block is logged, not an error
    row = fn("L", BLOCK, NOW + H)                                          # no nightly line for the day: logged, review still runs
    assert row["kind"] == "intraday" and len(row["regime_rules"]) == 1
    with pytest.raises(RV.ReviewError, match="no_new_direct_high"):
        fn("L", BLOCK, NOW + 2 * H)
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "logs" / "review-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kinds == ["regime_review_skipped_no_block", "nightly_missing_for_intraday", "regime_review_refused"]


def test_sealed_files_are_0600_and_dirs_0700(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub(json.dumps([KNOB])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    R.regime_review("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + H, commit=COMMIT, block=BLOCK, records=history())
    files = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert files and all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    for d in ("replies", "validator", "bundles", "playbooks", "manifests", "logs"):
        if (tmp_path / d).exists():
            assert stat.S_IMODE((tmp_path / d).stat().st_mode) == 0o700


# ------------------------------------------------------------------------------------------------ self-seal and hygiene
def test_replies_sealed_under_root_and_cli_prints_only_hashes(tmp_path, capsys):
    out = tmp_path / "root"
    mark(out)
    for arm in ("L", "L-nr", "P"):
        row, _ = R.genesis_manifest(arm, T0, COMMIT)
        R.append_manifest(out, arm, row, None, None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        R.review_night("L", DAY, root=out, ledger_root=tmp_path, model=stub(json.dumps([KNOB])), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    assert buf.getvalue() == ""                                         # the library prints nothing
    assert (out / "replies" / "L" / f"{DAY}.nightly.txt").read_text(encoding="utf-8") == json.dumps([KNOB])
    assert (out / "validator" / "L" / f"{DAY}.nightly.json").exists()
    p = R.printable(out, DAY)
    assert set(p) == {"manifest_chain", "decision_log_sha256"} and len(p["manifest_chain"]["L"]) == 2 and len(p["manifest_chain"]["P"]) == 1
    flat = json.dumps(p)
    assert "ret_15m" not in flat and "min_conviction" not in flat
    rc = R.main(["run", "--root", str(out), "--arm", "L-nr", "--day", DAY, "--commit", COMMIT, "--now-ms", str(NOW), "--stub"])
    cap = capsys.readouterr().out
    assert rc == 0 and set(json.loads(cap)) == {"manifest_chain", "decision_log_sha256"}


def test_decision_log_sha_changes_with_log(tmp_path):
    a = R.decision_log_sha256(tmp_path, DAY)
    (tmp_path / "decisions" / "L").mkdir(parents=True)
    (tmp_path / "decisions" / "L" / f"{DAY}.jsonl").write_text("x\n", encoding="utf-8")
    assert R.decision_log_sha256(tmp_path, DAY) != a


def test_no_r4b_l3_laya_and_no_hv4_paths_in_source():
    code = "import sys; import tools.hv6_review, tools.hv6_release; print([m for m in sys.modules if m.startswith(('tools.r4b','tools.l3','tools.laya'))])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=TOOLS.parent, check=True)
    assert out.stdout.strip() == "[]"
    src = (TOOLS / "hv6_review.py").read_text(encoding="utf-8")
    assert "haiku-v4" not in src and "bots.json" not in src and "ANTHROPIC" not in src


# ------------------------------------------------------------------------------------------------ import guard over every HV6 file
def _hv6_sources():
    files = sorted(TOOLS.glob("hv6_*.py"))
    assert {f.name for f in files} >= {"hv6_agents.py", "hv6_cards.py", "hv6_regime_block.py", "hv6_release.py", "hv6_review.py", "hv6_runner.py"}
    return files


def test_import_guard_runs_over_every_hv6_file_with_an_enumerated_code_only_set():
    assert HC.HV4_CODE_ONLY == frozenset({"hv4_arms", "hv4_budget", "hv4_model", "hv4_prompts", "hv4_review", "hv4_validate"})
    used = set()
    for f in _hv6_sources():
        src = f.read_text(encoding="utf-8")
        assert HC.forbidden_imports(src, HC.HV4_CODE_ONLY) == [], f.name
        used |= set(HC.forbidden_imports(src))
    assert used <= set(HC.HV4_CODE_ONLY)                                         # every exemption is one HV6 really imports
    assert HC.forbidden_imports("from tools import hv4_review") == ["hv4_review"]            # the default stays strict
    assert HC.forbidden_imports("import tools.hv4_runner", HC.HV4_CODE_ONLY) and HC.forbidden_imports("from tools import hv4_read", HC.HV4_CODE_ONLY)
    assert HC.forbidden_imports("from tools import arena_score", HC.HV4_CODE_ONLY)


HV4_OUTPUT_NAMES = {"load_trade_records", "review_night", "verify_chain", "active_version", "append_manifest", "read_manifests", "main", "genesis_manifest",
                    "make_manifest", "check_manifest", "load_version"}
HV4_OUTPUT_MODULES = {"hv4_runner", "hv4_read", "hv4_shakedown_report", "hv4_p0_count", "hv4_review_replay", "hv4_review_bench", "hv4_live_c0", "arena_score",
                      "arena_am6_read", "arena_report_legs", "arena_worst_gaps"}


def test_no_hv6_file_calls_hv4_chain_ledger_or_output_functions():
    import ast
    for f in _hv6_sources():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        aliases = {"RV"}
        wrappers = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[-1]
                names = {a.name for a in node.names}
                assert mod not in HV4_OUTPUT_MODULES and not names & HV4_OUTPUT_MODULES, f.name
                if mod == "hv4_review":
                    assert not names & (HV4_OUTPUT_NAMES | {"load_skip_records"}), f.name
                aliases |= {a.asname or a.name for a in node.names if a.name == "hv4_review"}
            elif isinstance(node, ast.Import):
                assert not {n.name.split(".")[-1] for n in node.names} & HV4_OUTPUT_MODULES, f.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in HV4_OUTPUT_NAMES | {"load_skip_records"}:
                v = node.value
                base = v.id if isinstance(v, ast.Name) else (v.attr if isinstance(v, ast.Attribute) else None)
                if base in aliases:
                    assert node.attr == "load_skip_records", f"{f.name}: reaches hv4_review.{node.attr}"
                    wrappers += 1
        if f.name == "hv6_review.py":                                            # RV.load_skip_records only inside the guarded wrapper
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load_skip_records")
            inside = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "load_skip_records"]
            assert wrappers == len(inside) == 1
        else:
            assert wrappers == 0, f.name
        if f.name not in ("hv6_agents.py", "hv6_cards.py"):                       # those two hold the deny-token list
            assert "haiku-v4" not in f.read_text(encoding="utf-8"), f.name


# ------------------------------------------------------------------------------------------------ pre-registration: the runner on main
def test_runner_names_equal_the_reviewers():
    from tools import hv6_runner as RUN
    for arm in ag.ARMS:
        assert RUN.bot_id(arm) == R.bot_id("v6.0", arm)                         # the engine-ledger dir name
    src = (TOOLS / "hv6_runner.py").read_text(encoding="utf-8")
    assert 'Path(a.root) / "engine-ledger"' in src and R.default_ledger_root("/x/haiku-v6") == "/x/haiku-v6/engine-ledger"
    assert 'self.dir = Path(root) / "decisions"' in (TOOLS / "arena_declog.py").read_text(encoding="utf-8")        # decisions/<arm>/<day>.jsonl
    assert "self.log.append(arm," in src
    # the runner has no release concept: it hard-codes hv6.0-, so no v6.N >= 1 can register until it takes the release from the registration
    assert "releases" not in src and RUN.BOT_PREFIX == "hv6.0-"
