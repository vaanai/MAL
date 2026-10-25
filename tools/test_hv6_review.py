"""Tests for tools/hv6_review.py. SYNTHETIC ledgers, cards and a hand-made regime block; StubModel only. No API call, no network, no key, no tape, and no
/data/mal/arena, HV4 or Q50 output is read (seals: hunt30/SEALS-BRIEF.md). Nothing here says the trader makes money or that a reviewer learns anything."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tools import hv4_review as RV
from tools import hv6_agents as ag
from tools import hv6_review as R
from tools.hv4_model import ModelReply
from tools.hv4_testkit import make_card

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
    assert row["effective_from_ms"] == max(CUT + RV.EFFECTIVE_OFFSET_MS, NOW)
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
    assert row["kind"] == "intraday" and row["intraday_n"] == 1 and row["effective_from_ms"] == t + RV.EFFECTIVE_OFFSET_MS
    assert row["block_as_of_ms"] < row["written_ms"] and row["max_input_close_ms"] < CUT
    assert row["playbook_sha256"] and len(row["regime_rules"]) == 1 and row["version"] == 2
    R.verify_chain(tmp_path, "L", reviewer_sha=R.reviewer_sha256(), regime_sha=R.regime_template_sha256(), frozen_sha=COMMIT)


def test_regime_review_caps_and_guards(tmp_path):
    setup(tmp_path)
    kw = dict(root=tmp_path, ledger_root=tmp_path, commit=COMMIT, block=BLOCK, records=history())
    with pytest.raises(RV.ReviewError, match="nights_review_first"):
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW, **kw)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + H, **kw)
    R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + 2 * H, **kw)
    with pytest.raises(RV.ReviewError, match="intraday_cap"):
        R.regime_review("L", DAY, model=stub("[]"), now_ms=NOW + 3 * H, **kw)
    with pytest.raises(RV.ReviewError, match="block_not_before_write"):
        R.regime_review("P", DAY, model=stub("[]"), now_ms=NOW, **{**kw, "block": {**BLOCK, "as_of_ms": NOW + 1}})
    with pytest.raises(RV.ReviewError, match="no_regime_review"):
        R.regime_review("L-nr", DAY, model=stub("[]"), now_ms=NOW, **kw)


def test_regime_review_truncated_is_abstain_and_only_regime(tmp_path):
    setup(tmp_path)
    R.review_night("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW, commit=COMMIT, records=history(), block=BLOCK)
    kw = dict(root=tmp_path, ledger_root=tmp_path, commit=COMMIT, block=BLOCK, records=history())
    row = R.regime_review("L", DAY, model=stub(json.dumps([REGIME]), stop_reason="max_tokens"), now_ms=NOW + H, **kw)
    assert row["review_error"] == "truncated" and row["changes"] == [] and row["regime_rules"] == []
    row2 = R.regime_review("L", DAY, model=stub(json.dumps([KNOB])), now_ms=NOW + 2 * H, **kw)
    assert row2["changes"] == [] and row2["rejected"] == [[0, "not_a_regime_rule"]] and row2["knobs"] == row["knobs"]


def test_direct_high_trigger_and_latest_block(tmp_path):
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
    bad(night, rows[:1], "effective_from", effective_from_ms=night["written_ms"] - 1)
    bad(night, rows[:1], "params_changed", params_sha256="0" * 64)
    bad(night, rows[:1], "intraday_n", intraday_n=1)
    bad(night, rows[:1], "regime_rule_expired_or_malformed", regime_rules=[{"expires_ms": 1}], changes=[])
    intra = R.regime_review("L", DAY, root=tmp_path, ledger_root=tmp_path, model=stub("[]"), now_ms=NOW + H, commit=COMMIT, block=BLOCK, records=history())
    bad(intra, rows, "intraday_changed_permanent_state", knobs={**intra["knobs"], "stop_pct": -20.0}, knobs_sha256=RV.sha256_hex(RV.canon({**intra["knobs"], "stop_pct": -20.0})))
    bad(intra, rows, "effective_from", effective_from_ms=intra["written_ms"])
    bad(intra, rows, "intraday_without_block", block_sha256=None, block_as_of_ms=None)


# ------------------------------------------------------------------------------------------------ loaders (synthetic files)
def _write_world(tmp, t0):
    root, led = tmp / "hv6root", tmp / "ledger"
    (root / "decisions" / "L").mkdir(parents=True)
    (led / "hv6-L").mkdir(parents=True)

    def pair(pid, entry, exit_, net):
        e = {"kind": "entry", "pos": True, "order_id": pid, "ms": entry, "size": 0.25, "conviction": 4, "plan": {"stop_pct": -25}, "why": "ok", "card": make_card(pid, entry)}
        t = {"kind": "trade", "position_id": pid, "stake_sol": 0.25, "decision_ms": {"entry": entry, "exit": exit_}, "exit_reason": "target",
             "legs": {"total": {"flat_1p9": {"net_lamports": int(net * 0.25e9)}}}}
        return e, t
    rows = [pair("A" * 40, t0 + 1000, t0 + 400_000, 0.1), pair("B" * 40, t0 - 5000, t0 + 400_000, 0.1), pair("C" * 40, CUT - 1000, CUT + 1000, 0.1)]
    (root / "decisions" / "L" / "2026-11-01.jsonl").write_text("\n".join(json.dumps(e) for e, _t in rows) + "\n", encoding="utf-8")
    (led / "hv6-L" / "trades-1.jsonl").write_text("\n".join(json.dumps(t) for _e, t in rows) + "\n", encoding="utf-8")
    return root, led


def test_loader_g4_t0_filter_and_cutoff(tmp_path):
    root, led = _write_world(tmp_path, T0)
    trades, open_n = R.load_trade_records("L", root, led, CUT, T0)
    assert [r["id"] for r in trades] == ["A" * 40]                    # B entered before T0; C closes after the cutoff
    assert trades[0]["net_pct"] == pytest.approx(10.0) and trades[0]["hold_s"] == 399
    assert open_n == 2                                                  # A's exit has no sell row here; C is open at the cutoff; B is pre-T0


def test_roots_with_deny_tokens_are_refused(tmp_path):
    for bad in ("/data/mal/arena/haiku-v4", str(tmp_path / "q50"), str(tmp_path / "bots.json")):
        with pytest.raises(RV.ReviewError):
            R.load_trade_records("L", bad, tmp_path, CUT, T0)
        with pytest.raises(RV.ReviewError):
            R.verify_chain(bad, "L")


# ------------------------------------------------------------------------------------------------ self-seal and hygiene
def test_replies_sealed_under_root_and_cli_prints_only_hashes(tmp_path, capsys):
    out = tmp_path / "root"
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
    rc = R.main(["run", "--root", str(out), "--ledger-root", str(tmp_path), "--arm", "L-nr", "--day", DAY, "--commit", COMMIT, "--now-ms", str(NOW), "--stub"])
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
