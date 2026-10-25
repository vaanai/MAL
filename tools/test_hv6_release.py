"""Tests for tools/hv6_release.py. Synthetic records only: no API call, no network, no sealed or HV4/Q50 data."""
from __future__ import annotations

import json
import os

import pytest

from tools import hv6_cards as HC
from tools import hv6_release as RL
from tools import hv6_review as R

DAY_MS = 86_400_000
CLOCK0 = R.day_start_ms("2026-10-30")
COMMIT = "a" * 40
COMMIT2 = "b" * 40
PIN = "c" * 64
MD5 = "d" * 32


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    real = os.path.realpath(tmp_path)
    monkeypatch.setattr(HC, "READ_ROOTS", (real,))
    monkeypatch.setattr(HC, "WRITE_ROOTS", (real,))
    monkeypatch.setattr(R, "wall_ms", lambda: CLOCK0 + 1)


SPEND = "owner-spend-line-1025"


def v60():
    return RL.build_release("v6.0", commit=COMMIT, clock_ms=CLOCK0, written_ms=CLOCK0 - 1)


def v61(prev, **kw):
    base = dict(commit=COMMIT, clock_ms=CLOCK0 + 12 * DAY_MS, written_ms=CLOCK0 + 11 * DAY_MS, params=R.Params(min_matches=15), prev=prev,
                fx_run_until_ms=CLOCK0 + 24 * DAY_MS, manifest_pin=PIN, fx_spend_line_notebook_id=SPEND)
    base.update(kw)
    return RL.build_release("v6.1", **base)


def code(fn):
    with pytest.raises(RL.ReleaseRefused) as e:
        fn()
    return str(e.value)


def test_v60_registers_all_five_with_ledger_names():
    r = v60()
    assert r["arms"] == ["L", "L-nr", "F", "P", "X"] and r["bot_ids"]["L"] == "hv6.0-L" and r["bot_ids"]["L-nr"] == "hv6.0-L-nr" and r["fx_run_until_ms"] is None
    assert r["sha256"] == RL.record_hash(r) and r["prev_release_sha256"] == "0" * 64
    assert code(lambda: RL.build_release("v6.0", commit=COMMIT, clock_ms=CLOCK0, written_ms=CLOCK0 - 1, arms=["L"])) == "v60_registers_all_five"
    assert code(lambda: RL.build_release("v6.0", commit=COMMIT, clock_ms=CLOCK0, written_ms=CLOCK0)) == "record_not_before_clock"
    assert code(lambda: RL.release_number("v7.0")) == "bad_release_id"
    for bad_clock in (0, CLOCK0 + 1):
        assert code(lambda: RL.build_release("v6.0", commit=COMMIT, clock_ms=bad_clock, written_ms=-5 if bad_clock == 0 else bad_clock - 1)) == "clock_not_a_utc_midnight"
    assert RL.bot_ids("v6.3", ["P"]) == {"P": R.bot_id("v6.3", "P")} == {"P": "hv6.3-P"}


def test_v61_registers_only_changed_arms_never_f_x():
    r = v61(v60())
    assert r["arms"] == ["L", "L-nr", "P"] and "F" not in r["bot_ids"] and "X" not in r["bot_ids"]
    assert r["bot_ids"]["L"] == "hv6.1-L" and r["params"]["min_matches"] == 15
    assert code(lambda: v61(v60(), arms=["L", "F"])) == "no_new_fx_copies"
    assert code(lambda: v61(v60(), arms=["L"])) == "arms_not_changed_set"


def test_nothing_changed_is_refused():
    assert code(lambda: v61(v60(), params=R.V60_PARAMS)) == "arms_not_changed_set"


def test_window_must_be_over():
    assert code(lambda: v61(v60(), clock_ms=CLOCK0 + 11 * DAY_MS, written_ms=CLOCK0)) == "window_not_over"


def test_refuses_unless_v60_f_and_x_continue():
    assert code(lambda: v61(v60(), fx_run_until_ms=None)) == "fx_not_continuing"
    assert code(lambda: v61(v60(), fx_run_until_ms=CLOCK0 + 24 * DAY_MS - 1)) == "fx_not_continuing"


def test_window_is_pinned_to_12_days():
    assert code(lambda: RL.build_release("v6.0", commit=COMMIT, clock_ms=CLOCK0, written_ms=CLOCK0 - 1, window_days=1)) == "window_not_pinned"
    assert code(lambda: RL.build_release("v6.0", commit=COMMIT, clock_ms=CLOCK0, written_ms=CLOCK0 - 1, window_days=30)) == "window_not_pinned"
    p = {**v60(), "window_days": 1}                                      # a forged short window in the previous record changes nothing: the constant is used
    p["sha256"] = RL.record_hash(p)
    assert code(lambda: v61(p, clock_ms=CLOCK0 + 2 * DAY_MS, written_ms=CLOCK0)) == "window_not_over"


def test_fx_continuation_needs_the_owner_spend_line_id():
    for bad in (None, "", "x", "has space"):
        assert code(lambda: v61(v60(), fx_spend_line_notebook_id=bad)) == "fx_spend_line_missing"
    assert v61(v60())["fx_spend_line_notebook_id"] == SPEND


def test_regime_only_release_marks_contrasts_not_decidable():
    prev = v60()
    both = v61(prev)                                                     # params change: L, L-nr, P together
    assert both["not_decidable_contrasts"] == []
    regime_only = {**prev, "procedure": {**prev["procedure"], "L": {**prev["procedure"]["L"], "regime": "old"}, "P": {**prev["procedure"]["P"], "regime": "old"}}}
    regime_only["sha256"] = RL.record_hash(regime_only)
    r = v61(regime_only, params=R.V60_PARAMS)
    assert r["arms"] == ["L", "P"] and r["not_decidable_contrasts"] == ["L - L-nr", "L-nr - P"]
    assert RL.not_decidable(["L", "L-nr", "P"]) == [] and RL.not_decidable(["L-nr"]) == ["L - L-nr", "L - P", "L-nr - P"]


def test_outcome_dependent_codes_are_not_release_inputs():
    assert "veto_cap" not in RL.BOUNDS_CODES | RL.SCHEMA_CODES and "unknown_evidence_id" not in RL.BOUNDS_CODES | RL.SCHEMA_CODES
    rows = [{"kind": "nightly", "reply_error": None, "review_error": None, "block_sha256": None,
             "rejected": [[0, "veto_cap"], [1, "unknown_evidence_id"], [2, "bad_keys"]]}]
    assert RL.metrics_from_manifests(rows)["schema_rejects"] == 1 and RL.metrics_from_manifests(rows)["bounds_rejects"] == 0


def test_prev_chain_checks():
    assert code(lambda: v61(None)) == "prev_release_missing"
    p = v60()
    assert code(lambda: v61({**p, "commit": COMMIT2})) == "prev_hash"


def test_data_only_needs_pin_and_code_change_needs_md5():
    assert code(lambda: v61(v60(), manifest_pin=None)) == "data_only_needs_manifest_pin"
    assert code(lambda: v61(v60(), commit=COMMIT2)) == "code_change_needs_md5_replay"
    r = v61(v60(), commit=COMMIT2, replay_md5=MD5, manifest_pin=None)
    assert r["replay_md5"] == MD5 and r["commit"] == COMMIT2


def test_params_outside_registry_cannot_even_be_built():
    with pytest.raises(R.ReviewError):
        R.Params(min_matches=12)


def test_release_inputs_are_non_outcome_only():
    ok = {"invalid_reply_rate": 0.1, "schema_rejects": 2, "bounds_rejects": 0, "token_cost_usd": 0.01, "latency_ms_p50": 900, "block_coverage": 1.0}
    assert v61(v60(), metrics=ok)["metrics"] == ok
    for bad in ("net_sol", "pnl", "arm_gap", "acceptance_rate", "effect_sign_mismatch_rejects", "total_net_pct"):
        assert code(lambda: v61(v60(), metrics={bad: 1})) == "outcome_metric_refused"
    assert code(lambda: v61(v60(), metrics={"foo": 1})) == "metric_not_whitelisted"
    assert code(lambda: v61(v60(), metrics={"token_cost_usd": -1})) == "metric_not_a_number"
    assert code(lambda: v61(v60(), metrics={"token_cost_usd": True})) == "metric_not_a_number"


def test_metrics_from_manifests_skip_outcome_dependent_codes():
    rows = [{"kind": "genesis"}, {"kind": "nightly", "reply_error": "not_json", "review_error": None, "rejected": [], "block_sha256": "x"},
            {"kind": "nightly", "reply_error": None, "review_error": None, "block_sha256": None, "regime_sha256": "r",
             "rejected": [[0, "bad_keys"], [1, "knob_out_of_bounds"], [2, "fewer_than_10_matches"], [3, "effect_sign_mismatch"], [4, "no_contrast"]]}]
    m = RL.metrics_from_manifests(rows)
    assert m == {"invalid_reply_rate": 0.5, "schema_rejects": 1, "bounds_rejects": 1, "block_coverage": 0.5}
    assert set(m) <= set(RL.ALLOWED_METRICS)
    RL.check_metrics(m)


def test_write_release_creates_genesis_for_reviewer_arms_only(tmp_path):
    p = v60()
    RL.write_release(tmp_path, p)
    for arm in ("L", "L-nr", "P"):
        assert len(R.verify_chain(tmp_path, arm, frozen_sha=COMMIT)) == 1
    assert not (tmp_path / "manifests" / "F.jsonl").exists() and not (tmp_path / "manifests" / "X.jsonl").exists()
    n = v61(p)
    RL.write_release(tmp_path, n)
    root1 = tmp_path / "releases" / "v6.1"
    rows = R.read_manifests(root1, "L")
    assert rows[0]["release"] == "v6.1" and rows[0]["params"]["min_matches"] == 15 and rows[0]["effective_from_ms"] == n["clock_ms"]
    assert len(R.read_manifests(tmp_path, "L")) == 1                                            # v6.0 chain untouched
    RL.write_release(tmp_path, n)                                                                # idempotent
    with pytest.raises(R.RV.ManifestRefused):
        RL.write_release(tmp_path, {**n, "bot_ids": {"L": "x"}, "sha256": RL.record_hash({**n, "bot_ids": {"L": "x"}})})


def test_tampered_record_refused(tmp_path):
    p = v60()
    assert code(lambda: RL.write_release(tmp_path, {**p, "commit": COMMIT2})) == "record_hash"


def test_cli_prints_only_the_record_sha(tmp_path, capsys):
    clock = str(CLOCK0)
    args = ["--root", str(tmp_path), "--release", "v6.0", "--commit", COMMIT, "--clock-ms", clock, "--written-ms", str(CLOCK0 - 1),
            "--metrics-json", json.dumps({"token_cost_usd": 0.5})]
    assert RL.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"release", "sha256"}
    assert RL.main(["--root", str(tmp_path), "--release", "v6.1", "--commit", COMMIT, "--clock-ms", str(CLOCK0), "--written-ms", str(CLOCK0 - 1),
                    "--params-json", '{"min_matches": 15}']) == 1
    assert "window_not_over" in capsys.readouterr().err


def test_procedure_changes_follow_the_inputs():
    a, b = RL.procedure(R.V60_PARAMS), RL.procedure(R.Params(skip_count=40))
    assert all(a[k] != b[k] for k in ("L", "L-nr", "P"))
    assert RL.procedure(R.V60_PARAMS) == a
