"""Offline tests for tools/c1nf_vmode_parity.py (#503 quant-proof item 7). Synthetic rows only: no tape, no network."""

from __future__ import annotations

import hashlib
import json

import numpy as np

from tools import c1nf_shadow as cs
from tools import c1nf_vmode_parity as vp
from tools.test_c1nf_shadow import B0, BT0, MINT, POOL, Q0, RAMP, S0, SPS, V0, StubModel, bt_of, sha_of, slot_of_sec

MINUTES = (10, 20)


def _models():
    return cs.ModelSet([{"from_day": "0000-00-00", "file": __file__, "sha256": sha_of(__file__)}], loader=lambda p: StubModel())


def _engine(v_source):
    """The real FeatureEngine (admission, V0, the event mapping); its decision is a fixed vector at MINUTES."""
    eng = cs.build_engine(None, v_source=v_source)
    want = {BT0 + 60 * m for m in MINUTES}
    eng.alive_pools = lambda T: [p for p in eng._pools if T in want]
    eng.features_at = lambda pool, T, sd=None: (np.full(cs.N_FEATURES, 0.5) + (T % 7), True, 0.1)
    return eng


def _batches(event_v, stamp=True, until=1400):
    rows = [{"_k": "migrations", "type": "complete", "mint": MINT, "slot": S0, "block_time": BT0}]
    for slot in range(S0, slot_of_sec(until)):
        r = {"_k": "trades", "venue": "pumpswap", "mint": MINT, "trader": f"w{slot % 7}", "side": "buy" if slot % 2 else "sell",
             "sol_lamports": 1_000_000, "token_raw": 1_000_000_000, "quote_reserve": Q0 + RAMP * (slot - S0), "base_reserve": B0, "pool": POOL,
             "slot": slot, "block_time": bt_of(slot), "tx_index": 1, "event_index": 0}
        if stamp:
            cs.stamp_replay_v(r, V0, event_v=event_v)
        else:
            r["virtual_quote_reserve"] = V0                                  # the tip key only
        rows.append(r)
    return [rows]


def _run(v_source, batches, **kw):
    return vp.run_rows(_engine(v_source), _models(), batches, v_source=v_source, decide_from=None, decide_to=None,
                       hour_sps={cs.hour_of(BT0): SPS, cs.hour_of(BT0 + 3600): SPS}, **kw)


def test_stamp_puts_v0_on_one_key_per_mode():
    assert cs.stamp_replay_v({}, V0, event_v=False) == {"virtual_quote_reserve": V0}
    assert cs.stamp_replay_v({}, V0, event_v=True) == {"virtual_quote_reserves": int(V0)}


def test_identity_day_event_equals_const_by_md5_and_no_outcome_is_kept():
    const = _run("const", _batches(False))
    ev = _run("event", _batches(True))
    assert len(const["picks"]) == len(MINUTES) and const["outcomes_dropped"] >= 1
    cmp = vp.compare(const["picks"], ev["picks"])
    assert cmp["equal"] and cmp["n_diff"] == 0 and cmp["a"]["md5"] == vp.picks_md5(ev["picks"])
    assert all(p["v_lamports"] == int(V0) for p in ev["picks"])


def test_event_mode_without_the_event_key_has_no_picks():
    ev = _run("event", _batches(True, stamp=False))
    assert ev["picks"] == [] and ev["universe"]["no_v_first_print"] == 1


def test_restart_matches_the_uninterrupted_run_after_the_restart_and_reports_the_held_book():
    R = BT0 + 15 * 60
    full = _run("event", _batches(True), snap_s=R)
    rs = _run("event", _batches(True), restart_s=R)
    assert [p["decision_T_ms"] for p in rs["picks"]] == [(BT0 + 1200) * 1000]                  # nothing decided before the restart
    cmp = vp.compare(full["picks"], rs["picks"], from_ms=R * 1000, held=full["held_at_snap"])
    assert cmp["equal"] and cmp["a"]["n"] == 1
    assert full["held_at_snap"] == [MINT]                                                     # the minute-10 pick is still in the book


def test_compare_lists_differences_and_flags_held_mints():
    a = [{"decision_T_ms": 1, "mint": "A", "x": 1}, {"decision_T_ms": 2, "mint": "B", "x": 1}]
    b = [{"decision_T_ms": 2, "mint": "B", "x": 2}, {"decision_T_ms": 3, "mint": "C", "x": 1}]
    cmp = vp.compare(a, b, held=["C"])
    assert not cmp["equal"] and cmp["n_diff"] == 3 and cmp["n_diff_mint_held"] == 1
    assert {(r["mint"], r["side"]) for r in cmp["diff"]} == {("A", "a_only"), ("B", "both"), ("C", "b_only")}


EMPTY_MD5 = hashlib.md5(b"").hexdigest()


def _ledger_root(tmp_path, day="2026-09-20"):
    snap = tmp_path / f"ledger-{day}" / "asof" / f"asof-{day}"
    snap.mkdir(parents=True)
    (snap / "MANIFEST.json").write_text(json.dumps({"asof_day": day}))
    return str(tmp_path / f"ledger-{day}")


def _no_read(monkeypatch):
    """Any read of the tape, the token table or the ledger fails the test: a refusal comes before all of them."""
    def boom(*a, **k):
        raise AssertionError("read before the refusal")
    for name in ("tape_rows", "hour_sps_from_tape", "AsofDirLedger"):
        monkeypatch.setattr(cs, name, boom)
    monkeypatch.setattr(vp, "load_pool_v", boom)


def test_main_refuses_non_exploration_days_and_bad_restart_before_reading(tmp_path, monkeypatch):
    _no_read(monkeypatch)
    L = ["--ledger-root", _ledger_root(tmp_path)]
    assert vp.main(["--day", "2026-10-01", "--tape-from", "2026-09-30T00", "--restart-at", "2026-10-01T12"] + L) == cs.EXIT_REFUSED
    assert vp.main(["--day", "2026-09-20", "--restart-at", "2026-09-21T12"] + L) == cs.EXIT_REFUSED
    assert vp.main(["--day", "2026-09-20", "--restart-at", "2026-09-19T12"] + L) == cs.EXIT_REFUSED
    assert vp.main(["--day", "2026-09-20", "--bootstrap-hours", "40"] + L) == cs.EXIT_REFUSED          # bootstrap before the tape start
    assert vp.main(["--day", "2026-09-20", "--tape", "/x/forward-1002ev"] + L) == cs.EXIT_REFUSED
    assert vp.main(["--day", "2026-09-20", "--ledger-root", "/var/lib/mal/ledger"]) == cs.EXIT_REFUSED   # forbidden ledger path


def test_main_refuses_a_run_without_a_ledger_before_reading(tmp_path, monkeypatch, capsys):
    """Review edit 2: no --ledger-root (job #505's run) or no asof-<day> under it is refused (exit 3) before anything is read."""
    _no_read(monkeypatch)
    assert vp.main(["--day", "2026-09-20"]) == cs.EXIT_REFUSED
    assert "--ledger-root is required" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert vp.main(["--day", "2026-09-20", "--ledger-root", str(empty)]) == cs.EXIT_REFUSED
    assert vp.main(["--day", "2026-09-20", "--ledger-root", _ledger_root(tmp_path, "2026-09-19")]) == cs.EXIT_REFUSED  # never asof-<D-1>


def test_main_with_zero_picks_does_not_exit_0(tmp_path, monkeypatch):
    """Review edit 1, job #505's shape: the run reads, nothing is picked, both md5s are the md5 of nothing. That is a FAIL, never exit 0."""
    monkeypatch.setattr(vp, "load_pool_v", lambda tokens: {})
    monkeypatch.setattr(cs, "hour_sps_from_tape", lambda tape, hours: {})
    monkeypatch.setattr(cs, "tape_rows", lambda *a, **k: iter([[]]))
    monkeypatch.setattr(cs, "AsofDirLedger", lambda root: object())
    real = cs.build_engine
    monkeypatch.setattr(cs, "build_engine", lambda ledger, *, v_source: real(None, v_source=v_source))
    out_json = tmp_path / "out.json"
    rc = vp.main(["--day", "2026-09-20", "--ledger-root", _ledger_root(tmp_path), "--out-json", str(out_json)])
    assert rc != 0 and rc == vp.EXIT_FAIL
    out = json.loads(out_json.read_text())
    assert out["verdict"] == "FAIL" and out["identity"]["equal"] and not out["identity"]["nonempty"]
    assert out["identity"]["a"] == {"n": 0, "md5": EMPTY_MD5} and not out["restart"]["nonempty"]
    assert not out["identity"]["decision_rows"]["nonempty"]
    assert [f.split(":")[0] for f in out["fail_reasons"]] == ["identity", "restart", "identity"]


def test_verdict_needs_equal_and_nonempty():
    p = [{"decision_T_ms": 1, "mint": "A"}]
    rows_ok = vp.compare_rows({"n": 3, "md5": "x"}, {"n": 3, "md5": "x"})
    empty = vp.compare([], [])
    assert empty["equal"] and not empty["nonempty"]
    assert vp.verdict({"identity": {**empty, "decision_rows": rows_ok}})[0] == vp.EXIT_FAIL
    good = {**vp.compare(p, p), "decision_rows": rows_ok}
    assert vp.verdict({"identity": good, "restart": vp.compare(p, p)}) == (vp.EXIT_PASS, [])
    assert vp.verdict({"identity": good, "restart": vp.compare(p, [])})[0] == vp.EXIT_FAIL                  # one side empty
    assert vp.verdict({"identity": {**good, "decision_rows": vp.compare_rows({"n": 3, "md5": "x"}, {"n": 3, "md5": "y"})}})[0] == vp.EXIT_FAIL
    assert vp.verdict({"identity": {**good, "decision_rows": vp.compare_rows({"n": 0, "md5": EMPTY_MD5}, {"n": 0, "md5": EMPTY_MD5})}})[0] \
        == vp.EXIT_FAIL
    assert vp.verdict({})[0] == vp.EXIT_FAIL


def test_decision_row_md5_covers_every_decision_row_and_matches_across_modes():
    const = _run("const", _batches(False))
    ev = _run("event", _batches(True), snap_s=BT0 + 15 * 60)
    assert const["tap_matches_counter"] and const["decision_rows"]["n"] == const["counters"]["decision_rows"] == len(MINUTES)
    assert ev["decision_rows"]["from_split"]["n"] == 1                                       # minute 20 only
    cmp = vp.compare_rows(const["decision_rows"], {k: ev["decision_rows"][k] for k in ("n", "md5")})
    assert cmp["equal"] and cmp["nonempty"]
    tap = vp.DecisionTap()
    tap.add(60, "P2", "h2")
    tap.add(60, "P1", "h1")
    assert tap.summary() == {"n": 2, "md5": hashlib.md5(b"60,P1,h1\n60,P2,h2\n").hexdigest()}  # sorted within one T
