"""a3_floor_flag_list: FLOOR drops the three BOOST-book A3 flags (default off, behaviour unchanged)."""
from __future__ import annotations

import json

from tools import floor_lane_executor as X
from tools.test_floor_lane_executor import Clock, FakeInner, STAKE, T0, a3_file, ledger_rows, lane_raw, utc, write_live_ok

OK = {"halt": False, "evaluated": True}
SET = {"halt": True, "evaluated": True}
UNEVAL = {"halt": False, "evaluated": False}


def mk(tmp, flags=None, on=None, run_utc=None, warn=None, raw_record=None):
    p = a3_file(tmp, flags, run_utc)
    if warn is not None:
        rec = json.loads(p.read_text())
        rec["warn"] = {"flags": warn}
        p.write_text(json.dumps(rec) + "\n")
    if raw_record is not None:
        p.write_text(raw_record)
    write_live_ok(tmp)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp))
    kw = {} if on is None else {"a3_floor_flag_list": on}
    return X.FloorLaneBridge(cfg, X.FloorLanePort(FakeInner(), cfg), lane, now_ms=Clock(T0), live_ok_path=live_ok, root_checks=False, a3_record_file=str(p), **kw)


def run(br):
    logged = []
    orig = br._log

    def spy(kind, **kw):
        logged.append((kind, kw))
        return orig(kind, **kw)
    br._log = spy
    br.a3_pause_tick(T0)
    return br._a3_pause, logged


def test_off_boost_flag_pauses_unchanged(tmp_path):
    pause, logged = run(mk(tmp_path, {"boost_share_low": SET}, on=False))
    assert pause == "a3_flags:boost_share_low"
    assert logged == [("a3_pause", {"why": pause})]


def test_default_is_off(tmp_path):
    br = mk(tmp_path, {"boost_share_low": SET})
    assert br.a3_floor_flag_list is False
    assert run(br)[0] == "a3_flags:boost_share_low"


def test_on_boost_flag_alone_no_pause(tmp_path):
    br = mk(tmp_path, {"boost_share_low": SET, "pins_changed": OK}, on=True)
    pause, logged = run(br)
    assert pause is None and logged == [("a3_ignored", {"ignored": ["boost_share_low"]})]
    assert br._lane_check(T0, STAKE) is None


def test_on_unevaluated_boost_flags_no_pause(tmp_path):
    pause, _ = run(mk(tmp_path, {"boost_last_slice_early": UNEVAL, "boost_budget_or_slices_changed": UNEVAL}, on=True))
    assert pause is None


def test_on_mixed_pins_changed_pauses_with_ignored(tmp_path):
    pause, logged = run(mk(tmp_path, {"boost_share_low": SET, "pins_changed": SET}, on=True))
    assert pause == "a3_flags:pins_changed"
    assert logged == [("a3_pause", {"why": pause, "ignored": ["boost_share_low"]})]


def test_on_resume_row_carries_ignored(tmp_path):
    br = mk(tmp_path, {"boost_share_low": SET, "boost_last_slice_early": SET}, on=True)
    br._a3_pause = "a3_flags:pins_changed"
    pause, logged = run(br)
    assert pause is None
    assert logged == [("a3_resume", {"why": None, "ignored": ["boost_last_slice_early", "boost_share_low"]})]


def test_on_ignored_set_change_alone_logs_only_a3_ignored(tmp_path):
    br = mk(tmp_path, {"boost_share_low": SET, "pins_changed": SET}, on=True)
    run(br)
    a3_file(tmp_path, {"boost_share_low": OK, "pins_changed": SET})
    br._a3_polled_ms = None
    pause, logged = run(br)
    assert pause == "a3_flags:pins_changed" and logged == [("a3_ignored", {"ignored": []})]


def sub(tmp, n):
    d = tmp / f"c{n}"
    d.mkdir()
    return d


def test_on_other_flags_still_pause(tmp_path):
    assert run(mk(sub(tmp_path, 0), {"boost_disabled": SET}, on=True))[0] == "a3_flags:boost_disabled"
    assert run(mk(sub(tmp_path, 1), {"some_new_flag": SET}, on=True))[0] == "a3_flags:some_new_flag"
    assert run(mk(sub(tmp_path, 2), {"pins_changed": OK}, on=True, warn={"program_changed": {"warn": True}}))[0] == "a3_flags:program_changed"


def test_on_problem_strings_unchanged(tmp_path):
    assert run(mk(sub(tmp_path, 0), {"pins_changed": OK}, on=True, run_utc=utc(T0 - 5 * 86_400_000)))[0] == "a3_record_stale"
    assert run(mk(sub(tmp_path, 1), on=True, raw_record="not json\n"))[0] == "a3_record_unreadable"
    assert run(mk(sub(tmp_path, 2), {}, on=True))[0] == "a3_record_no_halt_flags"
    assert run(mk(sub(tmp_path, 3), {"pins_changed": OK}, on=True, run_utc="bad"))[0] == "a3_record_no_run_utc"


def test_on_synthetic_share_high_alone_no_pause(tmp_path):
    assert run(mk(tmp_path, {"synthetic_share_high": SET}, on=True))[0] is None


def test_ignored_constant():
    assert X.FLOOR_A3_IGNORED_FLAGS == ("boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed")


def test_start_row_field_only_when_on(tmp_path):
    mk(tmp_path, on=True)
    start = [r for r in ledger_rows(tmp_path) if r["ev"] == "start"]
    assert start and start[-1].get("a3_floor_flag_list") is True
    off = tmp_path / "off"
    off.mkdir()
    mk(off)
    start = [r for r in ledger_rows(off) if r["ev"] == "start"]
    assert start and "a3_floor_flag_list" not in start[-1]


def test_a3_ignored_row_once_then_clears(tmp_path):
    br = mk(tmp_path, {"pins_changed": OK}, on=True)
    assert run(br) == (None, [])
    a3_file(tmp_path, {"pins_changed": OK, "boost_share_low": SET})
    br._a3_polled_ms = None
    pause, logged = run(br)
    assert pause is None and logged == [("a3_ignored", {"ignored": ["boost_share_low"]})]
    br._a3_polled_ms = None
    logged.clear()
    br.a3_pause_tick(T0)
    assert logged == []  # unchanged: no further row (spy is still installed on br._log)
    a3_file(tmp_path, {"pins_changed": OK})
    br._a3_polled_ms = None
    br.a3_pause_tick(T0)
    assert logged == [("a3_ignored", {"ignored": []})]


def test_flag_off_never_logs_a3_ignored(tmp_path):
    br = mk(tmp_path, {"pins_changed": OK}, on=False)
    run(br)
    a3_file(tmp_path, {"pins_changed": OK, "boost_share_low": SET})
    br._a3_polled_ms = None
    pause, logged = run(br)
    assert pause == "a3_flags:boost_share_low"
    assert all(k != "a3_ignored" for k, _ in logged) and br._a3_ignored == ()


def test_on_q3c_kept_cases_still_pause(tmp_path):
    """QP-RULING-1113-a3-burst-recheck Q3 (c): with the key on, every kept flag and record problem still pauses, also beside an ignored flag."""
    cases = [
        ({"pins_changed": SET}, {}, "a3_flags:pins_changed"),
        ({"pins_changed": UNEVAL, "boost_share_low": SET}, {}, "a3_flags:pins_changed"),
        ({"pins_changed": OK, "boost_disabled": UNEVAL}, {}, "a3_flags:boost_disabled"),
        ({"pins_changed": OK, "some_new_flag": UNEVAL}, {}, "a3_flags:some_new_flag"),
        ({"pins_changed": "garbage", "boost_share_low": SET}, {}, "a3_flags:pins_changed"),
        ({"pins_changed": {"halt": "yes", "evaluated": True}}, {}, "a3_flags:pins_changed"),
        ({"pins_changed": OK, "boost_share_low": SET}, {"run_utc": utc(T0 + 3_600_000)}, "a3_record_future"),
        ({"pins_changed": OK, "boost_share_low": SET}, {"run_utc": utc(T0 - 5 * 86_400_000)}, "a3_record_stale"),
    ]
    for i, (flags, kw, want) in enumerate(cases):
        assert run(mk(sub(tmp_path, i), flags, on=True, **kw))[0] == want, (i, want)


def test_on_record_error_pauses(tmp_path, monkeypatch):
    br = mk(tmp_path, {"pins_changed": OK}, on=True)
    monkeypatch.setattr(X.h5a, "a3_record_problem", lambda *a, **k: 1 / 0)
    assert run(br)[0] == "a3_record_error"


def test_run_parse_only_json_true_turns_key_on(tmp_path):
    """B3: run() turns the key on only for JSON true; "true" and 1 leave the start row without the key."""
    for i, (val, want_on) in enumerate([("true", False), (1, False), (True, True)]):
        d = sub(tmp_path, i)
        a3 = a3_file(d, {"pins_changed": OK}, None)
        raw = lane_raw(d, a3_record_file=str(a3), a3_floor_flag_list=val)
        write_live_ok(d)
        assert X.run(raw, live_flag=True, once=True, root_checks=False, port_factory=lambda c: X.FloorLanePort(FakeInner(), c), now_ms=Clock(T0), sleep=lambda s: None) == 0
        start = [r for r in ledger_rows(d) if r["ev"] == "start"]
        assert start, i
        assert ("a3_floor_flag_list" in start[-1]) is want_on, (val, start[-1])
