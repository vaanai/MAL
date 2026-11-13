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
    assert pause is None and logged == []
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


def test_on_ignored_set_change_alone_does_not_log(tmp_path):
    br = mk(tmp_path, {"boost_share_low": SET, "pins_changed": SET}, on=True)
    run(br)
    a3_file(tmp_path, {"boost_share_low": OK, "pins_changed": SET})
    br._a3_polled_ms = None
    pause, logged = run(br)
    assert pause == "a3_flags:pins_changed" and logged == []


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
