"""Tests for tools/hv7_tip_p0_count.py, the tip-source P0 counter. SYNTHETIC tape only (built here), no model, no API, no network, no sealed path (path guards are tested on names; nothing under
/data2/mal/arena-tip-feed, /var/lib/mal/sealed or the archive is opened). Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

import tools.haiku_caller as hcall
from tools import drill_hv7_real_layout as D
from tools import hv4_model as m4
from tools import hv7_runner as R
from tools import hv7_tip_p0_count as P
from tools.drill_hv7_real_layout import Refused
from tools.test_hv4_runner import T0, write_tape
from tools.test_hv7_runner import POOLS, tape_rows

DAYS = "2026-09-24,2026-09-25,2026-09-26"      # T0 of the synthetic tape is 2026-09-25T06:00Z
NOW = D.day_ms("2026-09-27")


@pytest.fixture()
def env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setattr(P, "FIRST_DECISION_DAY", "2026-09-01")
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def full_tape(d: Path, rows) -> None:
    """The synthetic rows plus an empty hour file for every other hour of the three days, so no day has a hole."""
    write_tape(d, rows)
    for day in DAYS.split(","):
        for h in range(24):
            f = d / f"tape-{day}T{h:02d}.jsonl"
            if not f.exists():
                f.write_text("")


# ------------------------------------------------------------------------------------------------------------------------ refusals (names only)
def test_tape_dir_accepts_only_the_receiver_copy_or_a_test_tmp_dir(tmp_path):
    assert str(P.check_tape_dir(R.TIP_FEED_ROOT)) == R.TIP_FEED_ROOT
    assert P.check_tape_dir(tmp_path) == Path(tmp_path).resolve()
    for p in ("/var/lib/mal/sealed/fast-trades-tip", "/data/mal/tip-tape-archive", "/data/mal/arena/tape", "/home/claude/mal-data/arena-mirror/tape",
              "/home/claude/mal-data/arena-tipfeed/tape", "/data2/mal/arena-tip-feed", "/data2/mal/arena-tip-feed/hv7x", "/data2/mal/arena-tip-feed/hv7/..", "/data/mal/blocks/forward-1016"):
        with pytest.raises(Refused, match="tape_dir_not_allowed"):
            P.check_tape_dir(p)
    for p in ("/data2/mal/arena-tip-feed/hv7/.writer", "/data2/mal/arena-tip-feed/hv7/.writer/state.json"):
        with pytest.raises(Refused, match="tip_writer_state"):
            P.check_tape_dir(p)
    with pytest.raises(Refused, match="tape_dir_not_absolute"):
        P.check_tape_dir("tape")
    sealed_in_tmp = tmp_path / "sealed" / "x"
    sealed_in_tmp.mkdir(parents=True)
    with pytest.raises(Refused, match="tape_dir_not_allowed"):
        P.check_tape_dir(sealed_in_tmp)


def test_days_must_be_three_contiguous_closed_days_from_2026_11_01():
    now = D.day_ms("2026-11-06")
    assert P.parse_days("2026-11-03,2026-11-04,2026-11-05", now_ms=now) == ["2026-11-03", "2026-11-04", "2026-11-05"]
    bad = {"need_exactly_3_distinct_days": ["2026-11-03,2026-11-04", "2026-11-03,2026-11-03,2026-11-04", "", "2026-11-02,2026-11-03,2026-11-04,2026-11-05"],
           "day_format": ["2026-11-03,2026-11-04,11-05", "2026-11-03,2026-11-04,2026-13-05"],
           "day_before_2026_11_01": ["2026-10-30,2026-10-31,2026-11-01", "2026-10-21,2026-10-22,2026-10-23"],
           "day_not_closed": ["2026-11-04,2026-11-05,2026-11-06"],
           "days_not_contiguous_ascending": ["2026-11-03,2026-11-05,2026-11-04", "2026-11-02,2026-11-03,2026-11-05"]}
    for why, args in bad.items():
        for a in args:
            with pytest.raises(Refused, match=why):
                P.parse_days(a, now_ms=now)


def test_notebook_ref_drill_id_and_root_are_required(env, tmp_path):
    full_tape(tmp_path / "tape", tape_rows())
    for ref in ("", "  ", None):
        with pytest.raises(Refused, match="notebook_ref_required"):
            P.run_p0(DAYS, "d1", ref, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)   # type: ignore[arg-type]
    with pytest.raises(Refused, match="drill_id"):
        P.run_p0(DAYS, "../x", "n_1", tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)
    with pytest.raises(Refused, match="not_a_drill_root"):
        P.run_p0(DAYS, "d1", "n_1", tape_dir=tmp_path / "tape", root=tmp_path / "elsewhere", now_ms=NOW)


def test_a_day_with_a_missing_hour_file_is_refused(env, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    full_tape(tmp_path / "tape", tape_rows())
    (tmp_path / "tape" / "tape-2026-09-24T13.jsonl").unlink()
    with pytest.raises(Refused, match="hours_missing"):
        P.run_p0(DAYS, "d1", "n_1", tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)


# ------------------------------------------------------------------------------------------------------------------------ the pass
def test_p0_counts_only_and_uses_the_runners_screen_with_u2(env, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    leader = POOLS[3]
    rows = [r for r in tape_rows() if not (r["type"] == "pool_create" and r["pool"] == leader)]     # one pool without a create row: U2 refuses it
    full_tape(tmp_path / "tape", rows)
    res = P.run_p0(DAYS, "d1", "n_fixed_days", tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)
    assert res["hv7_tip_p0"] == "counts" and res["outcome_free"] is True and res["notebook_ref"] == "n_fixed_days"
    assert res["hours_present"] == 72 and res["hours_missing"] == 0 and res["first_day"] == "2026-09-24" and res["last_day"] == "2026-09-26"
    pd = res["per_day"]
    assert [pd[d]["hours"] for d in sorted(pd)] == [24, 24, 24] and [pd[d]["half_hours"] for d in sorted(pd)] == [48, 48, 48]
    assert pd["2026-09-24"]["hours_ge3"] == 0 and pd["2026-09-26"]["hours_ge3"] == 0       # no rows on those days
    assert pd["2026-09-25"]["hours_ge3"] >= 1 and pd["2026-09-25"]["half_hours_ge3"] >= pd["2026-09-25"]["hours_ge3"]
    assert res["screen_counters"].get("u2_age_unknown", 0) >= 1                              # the runner's own screen applied U2
    r = res["rule"]
    assert r["pass_line_per_day"] == pytest.approx(150 / 14) and r["pass_hourly"] is False and r["hourly_any_day_lt_5"] is True
    text = json.dumps(res)
    for pool in POOLS.values():
        assert pool not in text                                                              # no pool id, mint or per-pool row is ever printed
    assert "Mint" not in text and "signature" not in text and "price" not in text


def test_cli_prints_a_refusal_reason_only_and_exits_2(capsys):
    rc = P.main(["--days", "2026-10-21,2026-10-22,2026-10-23", "--notebook-ref", "n_1", "--drill-id", "d1"])
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and json.loads(out.err) == {"hv7_tip_p0": "refused", "why": "day_before_2026_11_01"}
    rc = P.main(["--days", "2026-11-03,2026-11-04,2026-11-05", "--notebook-ref", "", "--drill-id", "d1"])
    assert rc == 2 and json.loads(capsys.readouterr().err)["why"] in ("notebook_ref_required", "day_not_closed")


# ------------------------------------------------------------------------------------------------------------------------ the rule
def per_day(*hourly):
    days = [f"2026-11-0{3 + i}" for i in range(len(hourly))]
    return days, {d: {"hours_ge3": h, "half_hours_ge3": 2 * h} for d, h in zip(days, hourly)}


def test_rule_pass_line_is_150_over_14_a_day_on_the_full_day_mean():
    days, pdy = per_day(11, 11, 11)
    assert P.rule(pdy, days, None)["pass_hourly"] is True
    days, pdy = per_day(10, 11, 11)                        # mean 10.667 < 10.714
    r = P.rule(pdy, days, None)
    assert r["pass_hourly"] is False and r["hourly_mean"] == pytest.approx(32 / 3) and r["pass_half_hour"] is True
    days, pdy = per_day(20, 20, 4)
    assert P.rule(pdy, days, None)["hourly_any_day_lt_5"] is True and "u2_undercount_days" not in P.rule(pdy, days, None)


def test_rule_flags_days_before_h0_plus_7d_as_the_declared_undercount():
    days, pdy = per_day(12, 12, 12)
    h0 = D.day_ms("2026-10-31")                              # H0 + 7 d = 2026-11-07T00Z: all three days (11-03..05) are inside the history shorter than 7 d
    assert P.rule(pdy, days, h0)["u2_undercount_days"] == days
    h0 = D.day_ms("2026-10-27")                              # H0 + 7 d = 2026-11-03T00Z: no day starts before it
    assert P.rule(pdy, days, h0)["u2_undercount_days"] == []
