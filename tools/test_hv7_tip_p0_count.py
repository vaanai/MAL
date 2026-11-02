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
from tools.test_hv4_runner import MIN, T0, write_tape
from tools.test_hv7_runner import POOLS, tape_rows

DAYS = "2026-09-25,2026-09-26,2026-09-27"      # T0 of the synthetic tape is 2026-09-25T06:00Z
H0 = "2026-09-24T22"                           # the history starts 8 h before T0: the shifted create rows (T0 - 8 h) lie in hour H0, before day 1
NOW = D.day_ms("2026-09-28")
HB = {"v": 1, "type": "hb", "prog": "pumpswap", "t_recv_ms": T0 - 90 * MIN, "signature": "hb0"}     # makes the state's periodic sweep run while the pools are idle past 6 h


@pytest.fixture()
def env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setattr(P, "FIRST_DECISION_DAY", "2026-09-01")
    monkeypatch.setattr(P, "H0_FLOOR", "2026-09-01T00")
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def shifted_rows(minutes: int = 300, drop: set | None = None) -> list[dict]:
    """The synthetic rows with every create row `minutes` earlier (so before day 1) and a heartbeat that triggers the state's sweep; `drop` removes the create rows of those pools."""
    out = []
    for r in [*tape_rows(), HB]:
        if r["type"] == "pool_create":
            if drop and r["pool"] in drop:
                continue
            r = {**r, "t_recv_ms": r["t_recv_ms"] - minutes * 60_000}
        out.append(r)
    return sorted(out, key=lambda r: (r["t_recv_ms"], r["signature"]))


def full_tape(d: Path, rows, h0: str = H0) -> None:
    """The rows plus an empty hour file for every other hour from H0 to the end of day 3, so no hour has a hole."""
    write_tape(d, rows)
    from datetime import datetime, timedelta, timezone

    t = datetime.strptime(h0, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime(2026, 9, 28, tzinfo=timezone.utc)
    while t < end:
        f = d / f"tape-{t.strftime('%Y-%m-%dT%H')}.jsonl"
        if not f.exists():
            f.write_text("")
        t += timedelta(hours=1)


def run(tmp_path, monkeypatch, rows, *, h0: str = H0, days: str = DAYS):
    from datetime import datetime, timezone

    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    hour = lambda r: datetime.fromtimestamp(r["t_recv_ms"] // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")  # noqa: E731
    full_tape(tmp_path / "tape", [r for r in rows if hour(r) >= h0], h0=h0)      # the dir holds only hours from H0 on (rows before it are not in the history)
    return P.run_p0(days, "d1", "n_fixed_days", h0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)


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


def test_h0_is_required_not_before_2026_10_16T01_and_before_day_1():
    days = ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert P.parse_h0("2026-10-31T00", days) == ("2026-10-31T00", D.day_ms("2026-10-31"))
    assert P.parse_h0("2026-10-16T01", days)[0] == "2026-10-16T01"
    for bad, why in ((None, "h0_format"), ("", "h0_format"), ("2026-10-31", "h0_format"), ("2026-10-31T24", "h0_format"), ("2026-10-16T00", "h0_before_2026_10_16T01"),
                     ("2026-10-02T10", "h0_before_2026_10_16T01"), ("2026-11-03T00", "h0_not_before_first_day"), ("2026-11-04T05", "h0_not_before_first_day")):
        with pytest.raises(Refused, match=why):
            P.parse_h0(bad, days)
    with pytest.raises(SystemExit):                                         # the command line cannot omit --h0
        P.main(["--days", "2026-11-03,2026-11-04,2026-11-05", "--notebook-ref", "n_1", "--drill-id", "d1"])


def test_notebook_ref_drill_id_and_root_are_required(env, tmp_path):
    full_tape(tmp_path / "tape", tape_rows())
    for ref in ("", "  ", None):
        with pytest.raises(Refused, match="notebook_ref_required"):
            P.run_p0(DAYS, "d1", ref, H0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)   # type: ignore[arg-type]
    with pytest.raises(Refused, match="drill_id"):
        P.run_p0(DAYS, "../x", "n_1", H0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)
    with pytest.raises(Refused, match="not_a_drill_root"):
        P.run_p0(DAYS, "d1", "n_1", H0, tape_dir=tmp_path / "tape", root=tmp_path / "elsewhere", now_ms=NOW)


@pytest.mark.parametrize("missing", ["tape-2026-09-24T23.jsonl", "tape-2026-09-26T13.jsonl"])        # a history hour and a counted-day hour
def test_a_missing_hour_file_from_h0_to_the_end_is_refused(env, tmp_path, monkeypatch, missing):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    full_tape(tmp_path / "tape", shifted_rows())
    (tmp_path / "tape" / missing).unlink()
    with pytest.raises(Refused, match="hours_missing"):
        P.run_p0(DAYS, "d1", "n_1", H0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)


def test_a_tape_hour_earlier_than_h0_in_the_dir_is_refused_by_name(env, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    full_tape(tmp_path / "tape", shifted_rows())
    (tmp_path / "tape" / "tape-2026-09-24T21.jsonl").write_text("")                         # one hour before H0 (2026-09-24T22)
    with pytest.raises(Refused, match="hours_before_h0_present"):
        P.run_p0(DAYS, "d1", "n_1", H0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)
    (tmp_path / "tape" / "tape-2026-09-24T21.jsonl").unlink()
    (tmp_path / "tape" / "tape-2026-09-24T21.jsonl.zst").write_bytes(b"")                  # a compacted early hour counts as well
    with pytest.raises(Refused, match="hours_before_h0_present"):
        P.run_p0(DAYS, "d1", "n_1", H0, tape_dir=tmp_path / "tape", root=tmp_path / "drill" / "d1", now_ms=NOW)


def test_the_system_temp_dir_is_the_only_test_root_and_scratch_is_not_one():
    assert P.TEST_TMP_ROOTS == (P.tempfile.gettempdir().rstrip("/") + "/",)
    with pytest.raises(Refused, match="tape_dir_not_allowed"):
        P.check_tape_dir("/data2/mal/scratch/hv7-tip-equiv/x")


def test_the_registered_p0_pins_h0_2026_10_29T09_and_all_three_days_are_undercount_days():
    days = ["2026-11-03", "2026-11-04", "2026-11-05"]
    key, ms = P.parse_h0("2026-10-29T09", days)
    assert ms == 1793264400000 and key == "2026-10-29T09"                                   # QP's pin, recomputed: date -u -d 2026-10-29T09:00:00Z
    assert P.PASS_PER_DAY == 150 / 14 == 10.714285714285714
    days_pd = {d: {"hours_ge3": 11, "half_hours_ge3": 22} for d in days}
    assert P.rule(days_pd, days, ms)["u2_undercount_days"] == days                           # H0 + 7 d = 2026-11-05T09Z: all three days start before it


# ------------------------------------------------------------------------------------------------------------------------ the pass
def test_pools_created_before_day_1_are_counted_when_the_state_is_built_from_h0(env, tmp_path, monkeypatch):
    """The reviewer's 4/0/0 case (B1 on #1172): every create row lies in the history hour H0, before day 1, and 6h50m before the pool's first trade (so the state's own 6 h idle
    eviction also forgets it). Loaded from H0 the day-1 screens see four known pools; loaded from day 1 on, U2 would refuse them all."""
    res = run(tmp_path, monkeypatch, shifted_rows(), h0=H0)
    pd = res["per_day"]
    assert res["hv7_tip_p0"] == "counts" and res["h0"] == H0 and res["history_hours_before_day_1"] == 2 and res["hours_present"] == 2 + 72
    assert [pd[d]["hours_ge3"] for d in sorted(pd)] == [4, 0, 0]
    assert res["screen_counters"].get("u2_age_unknown", 0) == 0
    assert [pd[d]["hours"] for d in sorted(pd)] == [24, 24, 24] and [pd[d]["half_hours"] for d in sorted(pd)] == [48, 48, 48]


def test_a_history_that_starts_after_the_create_rows_refuses_every_pool(env, tmp_path, monkeypatch):
    """The control: the same tape with H0 one hour after the create rows (H0 = 2026-09-24T23). Nothing is known, so U2 refuses every pool and the count is 0; the flag says what that is."""
    res = run(tmp_path, monkeypatch, shifted_rows(), h0="2026-09-24T23")
    assert [res["per_day"][d]["hours_ge3"] for d in sorted(res["per_day"])] == [0, 0, 0]
    assert res["screen_counters"]["u2_age_unknown"] > 0
    assert res["rule"]["u2_undercount_days"] == DAYS.split(",")                # H0 + 7 d = 2026-10-01T23Z: all three days start before it


def test_p0_counts_only_and_counts_the_pool_without_a_create_row_as_age_unknown(env, tmp_path, monkeypatch):
    res = run(tmp_path, monkeypatch, shifted_rows(drop={POOLS[3]}), h0=H0)
    pd = res["per_day"]
    assert pd["2026-09-25"]["hours_ge3"] >= 1 and res["screen_counters"].get("u2_age_unknown", 0) >= 1      # three known pools still screen; the fourth is counted and refused
    r = res["rule"]
    assert r["pass_line_per_day"] == pytest.approx(150 / 14) and r["pass_hourly"] is False and r["hourly_any_day_lt_5"] is True
    text = json.dumps(res)
    for pool in POOLS.values():
        assert pool not in text                                                              # no pool id, mint or per-pool row is ever printed
    assert "Mint" not in text and "signature" not in text and "price" not in text and res["outcome_free"] is True and res["notebook_ref"] == "n_fixed_days"


def test_cli_prints_a_refusal_reason_only_and_exits_2(capsys):
    rc = P.main(["--days", "2026-10-21,2026-10-22,2026-10-23", "--notebook-ref", "n_1", "--drill-id", "d1", "--h0", "2026-10-20T00"])
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and json.loads(out.err) == {"hv7_tip_p0": "refused", "why": "day_before_2026_11_01"}
    rc = P.main(["--days", "2026-11-03,2026-11-04,2026-11-05", "--notebook-ref", "", "--drill-id", "d1", "--h0", "2026-10-31T00"])
    assert rc == 2 and json.loads(capsys.readouterr().err)["why"] == "notebook_ref_required"


# ------------------------------------------------------------------------------------------------------------------------ the rule
def per_day(*hourly):
    days = [f"2026-11-0{3 + i}" for i in range(len(hourly))]
    return days, {d: {"hours_ge3": h, "half_hours_ge3": 2 * h} for d, h in zip(days, hourly)}


H0_MS = D.day_ms("2026-10-27")                               # H0 + 7 d = 2026-11-03T00Z


def test_rule_pass_line_is_150_over_14_a_day_on_the_full_day_mean():
    days, pdy = per_day(11, 11, 11)
    assert P.rule(pdy, days, H0_MS)["pass_hourly"] is True
    days, pdy = per_day(10, 11, 11)                        # mean 10.667 < 10.714
    r = P.rule(pdy, days, H0_MS)
    assert r["pass_hourly"] is False and r["hourly_mean"] == pytest.approx(32 / 3) and r["pass_half_hour"] is True
    days, pdy = per_day(20, 20, 4)
    assert P.rule(pdy, days, H0_MS)["hourly_any_day_lt_5"] is True


def test_rule_flags_every_day_that_starts_before_h0_plus_7d_and_the_key_is_always_present():
    days, pdy = per_day(12, 12, 12)
    assert P.rule(pdy, days, D.day_ms("2026-10-31"))["u2_undercount_days"] == days                      # all undercounted: the list is the full list, never []
    assert P.rule(pdy, days, D.day_ms("2026-10-28"))["u2_undercount_days"] == ["2026-11-03"]            # H0 + 7 d = 11-04T00Z: only 11-03 starts before it
    assert P.rule(pdy, days, H0_MS)["u2_undercount_days"] == []                                           # an empty list means no day is an undercount
