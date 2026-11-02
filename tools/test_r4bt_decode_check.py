"""r4bt_decode_check: glue tests with the a3_redeploy_check sample functions stubbed (no RPC, no network, no real sample)."""
from __future__ import annotations

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_decode_check as D

WRITE = {"name": "pump", "slot": 1000}
PINS = {"migrate": {"required_events": ["Complete"]}, "trailing_bytes": {}, "ignored_discs": {}}
R4PINS = {"frozen_sha": "a" * 40, "frozen_blobs": {"observe/trade_decode.py": "x"}}
T0 = 1_794_100_000_000


def good_ev():
    return {"seen_events": list(ARC.REQUIRED_EVENTS) + ["Complete"], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {}, "capped": 0,
            "n_swap_events": 0}


@pytest.fixture
def stubs(monkeypatch):
    calls = []
    monkeypatch.setattr(ARC, "frozen_guard", lambda p: calls.append("h") or dict(p["frozen_blobs"]))
    monkeypatch.setattr(ARC, "fetch_idls", lambda http_get, pins: ({}, []))
    monkeypatch.setattr(ARC, "idl_event_table", lambda idls: {})
    monkeypatch.setattr(ARC, "validate_trailing_pins", lambda t: {})
    monkeypatch.setattr(ARC, "fetch_sample", lambda client, slot: ([["x"]] * ARC.SAMPLE_MIN_TXS, 100))
    monkeypatch.setattr(ARC, "evaluate_sample", lambda sample, idls, ignored, trailing: good_ev())
    monkeypatch.setattr(ARC, "migrate_evidence", lambda *a, **k: good_ev())
    monkeypatch.setattr(ARC, "sample_failures", lambda ev, label, pins, fails, client=None: fails.extend(f"c: {label}: {k}" for k in ("undecodable",) if ev[k]))
    return calls


def run(now=T0, deploy=T0 - 3_600_000, adapter=lambda sample: [], pins=PINS, r4=R4PINS):
    return D.check_write(WRITE, pins, r4, client=None, http_get=None, now_ms=lambda: now, deploy_ms=deploy, adapter_check=adapter)


def test_pass_when_every_path_is_clean_and_h_runs_with_the_r4bt_pins(stubs):
    r = run()
    assert r["verdict"] == "PASS" and r["reasons"] == [] and r["at_ms"] == T0 and stubs == ["h"]


def test_fails_closed_without_the_adapter_path(stubs):
    r = run(adapter=None)
    assert r["verdict"] == "ERROR" and "not wired" in r["reasons"][0]


def test_adapter_path_failure_is_a_fail(stubs):
    r = run(adapter=lambda sample: ["event X maps to a different quote"])
    assert r["verdict"] == "FAIL" and "adapter path" in r["reasons"][0]


def test_timeout_after_24h_is_a_fail(stubs):
    r = run(now=T0 + 25 * 3_600_000, deploy=T0)
    assert r["verdict"] == "FAIL" and r["reasons"][-1].startswith("g:")


def test_short_sample_missing_required_event_and_idl_change_fail(stubs, monkeypatch):
    monkeypatch.setattr(ARC, "fetch_sample", lambda client, slot: ([["x"]], 300))
    monkeypatch.setattr(ARC, "evaluate_sample", lambda *a, **k: dict(good_ev(), seen_events=[]))
    monkeypatch.setattr(ARC, "fetch_idls", lambda http_get, pins: ({}, ["IDL sha256 differs"]))
    r = run()
    assert r["verdict"] == "FAIL" and any(x.startswith("c: sample has 1 txs") for x in r["reasons"]) and any(x.startswith("d:") for x in r["reasons"])
    assert "f: IDL sha256 differs" in r["reasons"]


def test_frozen_guard_error_and_missing_pin_are_errors(stubs, monkeypatch):
    def boom(p):
        raise ARC.CheckError("decoder files differ from the frozen book's")

    monkeypatch.setattr(ARC, "frozen_guard", boom)
    assert run()["verdict"] == "ERROR"
    monkeypatch.undo()
    assert run()["verdict"] == "ERROR"  # the real frozen guard against a wrong blob pin: never a PASS


def test_output_is_the_profile_the_a3_classifier_accepts_and_nothing_else(stubs):
    f = D.collect([run()])
    assert f["profile"] == A.PROFILE == D.PROFILE and f["redeploys"][0]["verdict"] == "PASS"
