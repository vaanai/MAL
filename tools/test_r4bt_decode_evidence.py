"""r4bt_decode_evidence (QP D1 on #1197): the decode-only migrate evidence. Synthetic fixtures, the migrate sample and the RPC stubbed: no network."""
from __future__ import annotations

import pytest

from tools import a3_redeploy_check as ARC
from tools import pump_structure_monitor as mon
from tools import r4bt_a3 as A
from tools import r4bt_decode_evidence as DE
from tools.test_r4bt_decode_parity import BOND, BUY, SELL, clean_sample, complete_event_blob, create_event_blob, create_pool_blob

T0 = 1_794_100_000_000
BLOBS = [BUY, SELL, BOND, create_pool_blob(), create_event_blob(), complete_event_blob()]


def grads(n, boost):
    return [{"slot": 1000 + i, "sig": f"s{i}", "mayhem": False, "quote_wsol": True, "init_boost": i < boost, "budget_lamports": 85_000_000_000 if i < boost else None,
             "boost": {"n_slices": 5} if i < boost else None} for i in range(n)]


def install(monkeypatch, n, boost):
    gs = grads(n, boost)
    fn = lambda client, min_slot, now_s: [({"slot": g["slot"]}, g) for g in gs]  # noqa: E731
    monkeypatch.setattr(ARC, "migrate_sample", fn)
    monkeypatch.setattr(mon, "tx_event_blobs", lambda tx: list(BLOBS))
    monkeypatch.setattr(mon, "annotate_mayhem", lambda client, g: None)
    monkeypatch.setattr(mon, "profile_boost", lambda client, pools, x: {"n_pools_skipped": 0})


def test_decode_fields_equal_migrate_evidence_when_boost_evidence_is_ample(monkeypatch):
    install(monkeypatch, 40, 30)
    old = ARC.migrate_evidence(None, {}, 1, 1e9, {}, {})
    new = DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {})
    assert all(new[k] == old[k] for k in DE.DECODE_FIELDS) and new["n_migrate_txs"] == 40 and new["n_events"] == 40 * len(BLOBS)
    assert "init_boost_share" in old and not any(k in new for k in ("init_boost_share", "budget_sol_median", "slices_median", "n_nonmayhem_wsol", "n_init_boost_graduations"))


def test_thin_boost_evidence_passes_the_wrapper_while_migrate_evidence_raises_and_no_boost_helper_is_called(monkeypatch):
    install(monkeypatch, 40, 0)
    with pytest.raises(ARC.CheckError, match="BOOST evidence"):
        ARC.migrate_evidence(None, {}, 1, 1e9, {}, {})
    monkeypatch.setattr(mon, "annotate_mayhem", lambda *a: pytest.fail("annotate_mayhem is BOOST-side RPC"))
    monkeypatch.setattr(mon, "profile_boost", lambda *a: pytest.fail("profile_boost is BOOST-side RPC"))
    assert DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {})["n_migrate_txs"] == 40


def test_too_few_migrate_transactions_is_still_an_error(monkeypatch):
    install(monkeypatch, ARC.MIGRATE_K_MIN - 1, 0)
    with pytest.raises(ARC.CheckError, match=f"need {ARC.MIGRATE_K_MIN}"):
        DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {})
    install(monkeypatch, ARC.MIGRATE_K_MIN, 0)
    assert DE.migrate_decode_evidence(None, {}, 1, 1e9, {}, {})["n_migrate_txs"] == ARC.MIGRATE_K_MIN


# ---- the whole check through the wrapper
WRITE = {"name": "pump", "slot": 1000}
PINS = {"migrate": {"required_events": ["Complete"]}, "trailing_bytes": {}, "ignored_discs": {}}
R4PINS = {"frozen_sha": "a" * 40, "frozen_blobs": {"observe/trade_decode.py": "x"}}


@pytest.fixture
def stubs(monkeypatch):
    big = clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)
    ev = {"seen_events": list(ARC.REQUIRED_EVENTS) + ["Complete"], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {}, "capped": 0,
          "n_swap_events": 0}
    monkeypatch.setattr(ARC, "frozen_guard", lambda p: dict(p["frozen_blobs"]))
    monkeypatch.setattr(ARC, "fetch_idls", lambda http_get, pins: ({}, []))
    monkeypatch.setattr(ARC, "idl_event_table", lambda idls: {})
    monkeypatch.setattr(ARC, "validate_trailing_pins", lambda t: {})
    monkeypatch.setattr(ARC, "fetch_sample", lambda client, slot: (big, 100))
    monkeypatch.setattr(ARC, "evaluate_sample", lambda *a, **k: dict(ev))
    monkeypatch.setattr(ARC, "sample_failures", lambda *a, **k: None)
    monkeypatch.setattr(DE, "migrate_decode_evidence", lambda *a, **k: dict(ev))


def check(adapter, now=T0, deploy=T0 - 3_600_000, pins=PINS):
    return DE.check_write(WRITE, pins, R4PINS, client=None, http_get=None, now_ms=lambda: now, deploy_ms=deploy, adapter_check=adapter)


def test_pass_path_is_reachable_with_thin_boost_and_the_a3_classifier_accepts_the_file(stubs, monkeypatch):
    """The REAL wrapper (not the stub) on a thin-BOOST migrate sample: ARC.migrate_evidence would raise here; the check PASSes."""
    monkeypatch.undo()
    install(monkeypatch, 40, 0)
    ev = {"seen_events": list(ARC.REQUIRED_EVENTS) + ["Complete"], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {}, "capped": 0,
          "n_swap_events": 0, "nonzero_trailing": [], "trailing_seen": {}, "n_events": 1}
    big = clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)
    for name, fn in (("frozen_guard", lambda p: dict(p["frozen_blobs"])), ("fetch_idls", lambda h, p: ({}, [])), ("validate_trailing_pins", lambda t: {}),
                     ("fetch_sample", lambda c, s: (big, 100)), ("evaluate_sample", lambda *a, **k: dict(ev)), ("evaluate_blobs", lambda *a, **k: dict(ev)),
                     ("sample_failures", lambda *a, **k: None)):
        monkeypatch.setattr(ARC, name, fn)
    with pytest.raises(ARC.CheckError, match="BOOST evidence"):
        ARC.migrate_evidence(None, {}, 1, 1e9, {}, {})
    r = check(lambda sample: [])
    assert r["verdict"] == "PASS" and r["reasons"] == []
    assert DE.collect([r])["profile"] == A.PROFILE == DE.PROFILE


def test_parity_mismatch_is_a_fail(stubs):
    r = check(lambda sample: ["trade.value:sol_lamports x3"])
    assert r["verdict"] == "FAIL" and r["reasons"] == ["c: adapter path: trade.value:sol_lamports x3"]


def test_too_few_migrate_transactions_through_the_check_is_error_and_a_missing_adapter_is_error(stubs, monkeypatch):
    def few(*a, **k):
        raise ARC.CheckError(f"migrate sample: 3 settled post-deploy migrate txs, need {ARC.MIGRATE_K_MIN}")

    monkeypatch.setattr(DE, "migrate_decode_evidence", few)
    r = check(lambda sample: [])
    assert r["verdict"] == "ERROR" and "need 20" in r["reasons"][0]
    monkeypatch.undo()
    assert check(None)["verdict"] == "ERROR"


def test_required_migrate_event_missing_timeout_and_pin_missing(stubs, monkeypatch):
    assert check(lambda s: [], pins=dict(PINS, migrate={"required_events": ["Complete", "CreatePool"]}))["reasons"] == ["d: CreatePool not seen in the migrate sample"]
    assert check(lambda s: [], now=T0 + 25 * 3_600_000, deploy=T0)["reasons"][-1].startswith("g:")
    assert check(lambda s: [], pins=dict(PINS, migrate={}))["verdict"] == "ERROR"


def test_the_wrapper_never_calls_the_boost_helpers():
    import ast

    tree = ast.parse(open(DE.__file__, encoding="utf-8").read())
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"annotate_mayhem", "profile_boost", "migrate_evidence"}
