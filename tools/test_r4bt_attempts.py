"""Tests for tools/r4bt_attempts.py (r4-b-T's outcome-blind attempt counter). Synthetic decision logs in tmp dirs only; no real declog is opened."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import arena_declog
from tools import r4bt_attempts as A
from tools.arena_declog import DecisionLog

ARM = "r4b-exp027"
SENTINEL_MINT = "SYNmintSENTINEL1111111111111111111111111pump"


def ms_of(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


CLOCK = ms_of("2026-11-07T06:00:00")  # 1794031200000
assert CLOCK == 1794031200000


class Book:
    """Writes rows the way tools/r4b_live writes them (start, exits first, then entries in rank order, gap rows), through the real DecisionLog."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.log = DecisionLog(root)
        self.n = 0

    def start(self, ms: int) -> None:
        self.log.append(ARM, ms, {"kind": "start", "book": "r4-b", "exp": "EXP-027", "mode": "dry", "frozen_sha": "b" * 40, "warmup_s": 187200})

    def mark(self, t_mark_s: int, n_entries: int, decide_lag_ms: int = 1200, n_exits: int = 1) -> None:
        ms = t_mark_s * 1000 + decide_lag_ms
        for i in range(n_exits):
            self.log.append(ARM, ms, {"kind": "exit", "order_id": f"X{i}:{t_mark_s - 3600}", "mint": SENTINEL_MINT, "reason": "rank", "t_mark": t_mark_s,
                                      "decision_slot": 463000000})
        for r in range(n_entries):
            self.n += 1
            self.log.append(ARM, ms, {"kind": "entry", "order_id": f"{SENTINEL_MINT}{self.n}:{t_mark_s}", "pool": "SYNpoolSENTINEL",
                                      "meta": {"mint": SENTINEL_MINT, "v0": 17580000000, "mbt": t_mark_s - 7200, "s0": 1, "t_mark": 0, "sha": "f" * 64},
                                      "stake_sol": 0.25, "decision_slot": 463000000, "t_mark": t_mark_s, "rank": r + 1, "score": 0.123456789})

    def gap(self, ms: int) -> None:
        self.log.append(ARM, ms, {"kind": "gap", "gap_kind": "tip_gap_backlog_jump", "from_ms": ms - 60000, "to_ms": ms, "data_loss": True})


def hours(start: str, n: int) -> list[int]:
    s = ms_of(start) // 1000
    return [s + 3600 * i for i in range(n)]


def table(fr: str = "2026-11-07T06", th: str = "2026-11-30T23", excluded=(), path: Path | None = None) -> A.HourTable:
    obj = {"tabled_from": fr, "tabled_through": th, "excluded_hours": list(excluded)}
    if path is None:
        return A.HourTable(A.hour_start_ms(fr), A.hour_start_ms(th), frozenset(excluded), None)
    path.write_text(json.dumps(obj))
    return A.load_hour_table(path)


def test_pinned_constants():
    assert (A.GATE_ATTEMPTS, A.GATE_DATES, A.EXCLUDED_CAP, A.P0_FLOOR_MEAN) == (100, 5, 0.10, 2.0)
    assert (A.H_MIN_DAYS, A.H_MAX_DAYS, A.H_NUM) == (14, 90, 150)
    assert A.ARM_DEFAULT == "r4b-exp027"
    assert A.COUNT_KEYS == ("kind", "ms", "t_mark") and A.CHAIN_KEYS == ("seq", "prev", "sha")
    assert A.GENESIS == arena_declog.GENESIS


def test_r4b_live_bot_id_matches_the_default_arm():
    # the frozen code's arm name, read as text (no tools/r4b_* import from the counter or this test)
    src = (Path(__file__).resolve().parent / "r4b_live.py").read_text()
    assert 'BOT_ID = "r4b-exp027"' in src


def test_counter_is_stdlib_only_and_number_neutral():
    src = Path(A.__file__).read_text()
    assert "import r4b_" not in src and "from tools.r4b_" not in src and "tools import r4b" not in src
    assert not re.search(r"^\s*(from|import)\s.*arena_(declog|paper)", src, re.M)  # stdlib only: arena_declog pulls arena_paper and numpy
    assert "Amendment 25" not in src and "Amendment 24" not in src and "Amendment 26" not in src


def test_h_days_formula_exact():
    assert A.h_days(309, 3) == 14  # about 103 a day
    assert A.h_days(32, 3) == 15  # rate 10.67: ceil(14.0625)
    assert A.h_days(33, 3) == 14  # rate 11: ceil(13.6)
    assert A.h_days(15, 3) == 30  # rate 5
    assert A.h_days(3, 3) == 90  # rate 1: ceil(150) capped at 90
    assert A.h_days(0, 3) is None


# ---------------------------------------------------------------------------------------------------------- reading: keep-only fields and the chain
def test_the_parser_keeps_only_counting_and_chain_keys():
    row = json.loads(json.dumps({"kind": "entry", "ms": 5, "t_mark": 4, "seq": 0, "prev": "0" * 64, "sha": "a" * 64, "mint": "M",
                                 "meta": {"mint": "M", "t_mark": 1}, "score": 0.9, "rank": 1}), object_pairs_hook=A._keep_only)
    assert row == {"kind": "entry", "ms": 5, "t_mark": 4, "seq": 0, "prev": "0" * 64, "sha": "a" * 64}


def test_every_parse_uses_the_keep_only_hook(tmp_path, monkeypatch):
    b = Book(tmp_path)
    b.mark(CLOCK // 1000, 3)
    real = json.loads
    seen = []

    def spy(s, *a, **kw):
        seen.append(kw.get("object_pairs_hook"))
        return real(s, *a, **kw)

    monkeypatch.setattr(A.json, "loads", spy)
    lg = A.read_log(tmp_path, ARM)
    assert len(seen) == 4 and all(h is A._keep_only for h in seen)
    for r in lg.rows:
        assert set(r) <= set(A.COUNT_KEYS)


def test_streamed_chain_check_equals_arena_declog_verify(tmp_path):
    b = Book(tmp_path)
    b.start(CLOCK - 3600 * 1000)
    for t in hours("2026-11-07T06:00:00", 30):
        b.mark(t, 3, n_exits=2)
    b.gap(ms_of("2026-11-08T12:30:00"))
    lg = A.read_log(tmp_path, ARM)
    assert lg.head == DecisionLog(tmp_path).verify(ARM)
    assert lg.head[1] == len(lg.rows) - 1


@pytest.mark.parametrize("mut", ["content", "prev", "seq", "sha_twice", "noncanonical"])
def test_streamed_chain_check_catches_tampering(tmp_path, mut):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 3):
        b.mark(t, 2)
    f = sorted((tmp_path / "decisions" / ARM).glob("*.jsonl"))[0]
    lines = f.read_bytes().split(b"\n")
    if mut == "content":
        lines[1] = lines[1].replace(b'"rank":1', b'"rank":2')
    elif mut == "prev":
        row = json.loads(lines[2])
        lines[2] = lines[2].replace(row["prev"].encode(), b"1" * 64)
    elif mut == "seq":
        lines[1] = lines[1].replace(b'"seq":1,', b'"seq":7,')
    elif mut == "sha_twice":
        row = json.loads(lines[1])  # a second exact "sha":"<hex>" token, nested
        lines[1] = lines[1].replace(b'"pool":', b'"q":{"sha":"' + row["sha"].encode() + b'"},"pool":')
    elif mut == "noncanonical":
        lines[1] = lines[1].replace(b'","', b'", "', 1)  # whitespace: not canonical, same parsed row
    f.write_bytes(b"\n".join(lines))
    with pytest.raises(A.Untrusted):
        A.read_log(tmp_path, ARM)
    if mut == "noncanonical":
        DecisionLog(tmp_path).verify(ARM)  # arena_declog re-canonicalises and passes; the stream check fails closed (stricter)


def test_torn_and_unparseable_lines_are_untrusted(tmp_path):
    b = Book(tmp_path)
    b.mark(CLOCK // 1000, 2)
    f = sorted((tmp_path / "decisions" / ARM).glob("*.jsonl"))[0]
    f.write_bytes(f.read_bytes() + b'{"kind":"entry","ms":')
    with pytest.raises(A.Untrusted):
        A.read_log(tmp_path, ARM)
    with pytest.raises(A.Untrusted):
        A.read_log(tmp_path, ARM, verify=False)
    f.write_bytes(f.read_bytes() + b"\n")
    with pytest.raises(A.Untrusted):
        A.read_log(tmp_path, ARM, verify=False)


# ---------------------------------------------------------------------------------------------------------- p0
def test_p0_counts_entries_per_closed_day_and_nothing_else(tmp_path):
    b = Book(tmp_path)
    b.start(ms_of("2026-11-04T00:00:00") - 5000)
    for t in hours("2026-11-04T00:00:00", 72):
        b.mark(t, n_entries=4, n_exits=2)
    b.gap(ms_of("2026-11-06T23:30:00"))  # in time order, as r4b_live.log_gap keeps the log
    out = A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-05", "2026-11-06"], ms_of("2026-11-07T00:30:00"))
    assert out["days"] == {"2026-11-04": 96, "2026-11-05": 96, "2026-11-06": 96}
    assert out["total"] == 288 and out["min_day"] == 96 and out["floor_ok"] is True and out["h_days"] == 14
    assert out["chain_verified"] is True and out["chain_seq"] >= 0 and len(out["chain_head"]) == 64
    assert SENTINEL_MINT not in json.dumps(out) and "SYNpool" not in json.dumps(out)


def test_p0_floor_fails_on_a_zero_day_and_on_a_low_mean(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-04T00:00:00", 48):
        b.mark(t, n_entries=1)
    out = A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-05", "2026-11-06"], ms_of("2026-11-07T00:30:00"))
    assert out["days"]["2026-11-06"] == 0 and out["floor_ok"] is False
    b2 = Book(tmp_path / "low")
    b2.mark(ms_of("2026-11-04T10:00:00") // 1000, 1)
    b2.mark(ms_of("2026-11-05T10:00:00") // 1000, 1)
    b2.mark(ms_of("2026-11-06T10:00:00") // 1000, 1)
    out2 = A.p0(tmp_path / "low", ARM, ["2026-11-04", "2026-11-05", "2026-11-06"], ms_of("2026-11-07T00:30:00"))
    assert out2["mean_per_day"] == 1.0 and out2["floor_ok"] is False and out2["h_days"] == 90


def test_p0_refuses_an_open_day_and_fewer_than_three_days(tmp_path):
    Book(tmp_path).mark(ms_of("2026-11-04T10:00:00") // 1000, 1)
    with pytest.raises(A.Refused):
        A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-05", "2026-11-06"], ms_of("2026-11-06T23:59:00"))
    with pytest.raises(A.Refused):
        A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-05"], ms_of("2026-11-07T01:00:00"))
    with pytest.raises(A.Refused):
        A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-04", "2026-11-05"], ms_of("2026-11-07T01:00:00"))


# ---------------------------------------------------------------------------------------------------------- count
def test_count_boundary_is_the_first_midnight_with_100_attempts_on_5_dates(tmp_path):
    b = Book(tmp_path)
    b.start(CLOCK - 3 * 3600 * 1000)
    b.mark(CLOCK // 1000 - 3600, 5)  # 05:00Z mark decided before the clock: never counted
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-15T12:00:00"), table())
    assert out["not_counted_before_clock"] == 5
    assert out["counted_per_date"]["2026-11-07"] == 18  # 06Z..23Z
    # cumulative: 11-07 18, 11-08 42, 11-09 66, 11-10 90, 11-11 114 -> 5 dates and >= 100 at 2026-11-12T00:00Z
    assert out["status"] == "boundary" and out["boundary"] == "2026-11-12T00:00:00Z"
    assert out["attempts_before_boundary"] == 114 and out["dates_before_boundary"] == 5
    assert out["excluded_hour_before_boundary"] == 0 and out["excluded_cap_exceeded"] is False
    assert SENTINEL_MINT not in json.dumps(out)


def test_count_dates_bind_before_attempts(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 6):
        b.mark(t, 10)  # 180 on the first partial date already, but 5 dates are needed
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-14T00:00:00"), table())
    assert out["boundary"] == "2026-11-12T00:00:00Z" and out["dates_before_boundary"] == 5


def test_count_boundary_not_reported_before_it_comes(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-11T23:59:00"), table())
    assert out["status"] == "pending" and out["boundary"] is None


def test_count_withholds_the_boundary_without_a_covering_hour_table(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    as_of = ms_of("2026-11-15T00:00:00")
    none = A.count(tmp_path, ARM, CLOCK, 14, as_of, None)
    assert none["status"] == "pending_hour_table" and none["boundary"] is None and none["hour_table"] is None
    short = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(th="2026-11-11T22"))  # the hour 11-11T23 is not tabled
    assert short["status"] == "pending_hour_table" and short["boundary"] is None
    late_start = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(fr="2026-11-07T07"))  # the clock hour is not tabled
    assert late_start["status"] == "pending_hour_table"
    exact = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(th="2026-11-11T23"))
    assert exact["status"] == "boundary" and exact["boundary"] == "2026-11-12T00:00:00Z"


def test_count_mark_before_clock_written_after_it_is_not_counted(tmp_path):
    b = Book(tmp_path)
    b.mark(CLOCK // 1000 - 3600, 2, decide_lag_ms=3600 * 1000 + 5000)  # a 05:00Z mark written at 06:00:05Z
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-08T00:00:00"), table())
    assert out["not_counted_mark_before_clock"] == 2 and out["counted_total"] == 0


def test_count_window_end_and_not_decidable_even_without_a_table(tmp_path):
    b = Book(tmp_path)
    for d in range(16):
        b.mark(CLOCK // 1000 + 86400 * d, 1)  # 1 attempt a day: never 100 by clock + 14 d
    for tab in (table(), None):
        out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-22T00:00:00"), tab)
        assert out["status"] == "ended_not_decidable" and out["boundary"] is None
        assert out["counted_total"] == 14 and out["not_counted_after_window"] == 2
        assert out["window_end"] == "2026-11-21T06:00:00Z"


def test_count_excluded_hours_leave_the_count_and_feed_the_cap(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    ex = {f"2026-11-0{d}T{h:02d}" for d in (8, 9) for h in range(0, 12)}  # 24 hours excluded
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-15T00:00:00"), table(excluded=ex))
    assert out["excluded_hour_total"] == 24 and out["excluded_hour_per_date"] == {"2026-11-08": 12, "2026-11-09": 12}
    # counted: 11-07 18, 11-08 12, 11-09 12, 11-10 24, 11-11 24 = 90 < 100 at 11-12; 11-12 +24 = 114 at 11-13
    assert out["boundary"] == "2026-11-13T00:00:00Z"
    assert out["attempts_before_boundary"] == 114 and out["excluded_hour_before_boundary"] == 24
    assert out["excluded_share"] == pytest.approx(24 / 138) and out["excluded_cap_exceeded"] is True


def test_count_refuses_bad_clock_and_h(tmp_path):
    Book(tmp_path).mark(CLOCK // 1000, 1)
    with pytest.raises(A.Refused):
        A.count(tmp_path, ARM, CLOCK + 1000, 14, CLOCK + 10)
    with pytest.raises(A.Refused):
        A.count(tmp_path, ARM, CLOCK, 13, CLOCK + 10)
    with pytest.raises(A.Refused):
        A.count(tmp_path, ARM, CLOCK, 91, CLOCK + 10)


def test_hour_table_validation(tmp_path):
    p = tmp_path / "t.json"
    t = table("2026-11-07T06", "2026-11-08T05", ["2026-11-07T09"], path=p)
    assert t.covers(CLOCK, CLOCK + 24 * 3600 * 1000) and not t.covers(CLOCK, CLOCK + 25 * 3600 * 1000) and len(t.sha256) == 64
    for bad in ({"excluded_hours": []}, {"tabled_from": "2026-11-08T00", "tabled_through": "2026-11-07T00", "excluded_hours": []},
                {"tabled_from": "2026-11-07T06", "tabled_through": "2026-11-08T05", "excluded_hours": ["2026-11-09T00"]},
                {"tabled_from": "2026-11-07T06", "tabled_through": "2026-11-08T05", "excluded_hours": ["2026-11-07 07"]}, ["x"]):
        p.write_text(json.dumps(bad))
        with pytest.raises(A.Refused):
            A.load_hour_table(p)


# ---------------------------------------------------------------------------------------------------------- paths and CLI
def test_forbidden_roots_are_refused_also_through_symlinks(tmp_path):
    for p in ("/data/mal/r4b", "/data/mal/r4b/reads", "/var/lib/mal/sealed/fast-trades-tip", str(tmp_path / "tip-tape-archive" / "x")):
        with pytest.raises(A.Refused):
            A.check_root(p)
    assert A.check_root(tmp_path) == tmp_path.resolve()
    real = tmp_path / "real"
    real.mkdir()
    named = tmp_path / "tip-tape-archive-view"
    os.symlink(real, named)  # the given path carries the mark, the resolved one does not
    with pytest.raises(A.Refused):
        A.check_root(named)
    sealedish = tmp_path / "tip-tape-archive"
    sealedish.mkdir()
    innocent = tmp_path / "declog"
    os.symlink(sealedish, innocent)  # the given path is clean, the resolved one is not
    with pytest.raises(A.Refused):
        A.check_root(innocent)
    # a day file that is a symlink into a closed place is refused too
    b = Book(tmp_path / "book")
    b.mark(CLOCK // 1000, 1)
    target = sealedish / "2026-11-08.jsonl"
    target.write_text("")
    os.symlink(target, tmp_path / "book" / "decisions" / ARM / "2026-11-08.jsonl")
    with pytest.raises(A.Refused):
        A.read_log(tmp_path / "book", ARM)


def test_cli_count_p0_day_sha_and_refusals(tmp_path, capsys):
    root = tmp_path / "declog"
    b = Book(root)
    for t in hours("2026-11-04T00:00:00", 24 * 4):
        b.mark(t, 4)
    now = ms_of("2026-11-08T00:30:00")
    assert A.main(["p0", "--declog-root", str(root), "--days", "2026-11-04,2026-11-05,2026-11-06"], now_ms=now) == 0
    p0 = json.loads(capsys.readouterr().out)
    assert p0["days"] == {"2026-11-04": 96, "2026-11-05": 96, "2026-11-06": 96} and p0["chain_verified"] is True
    hx = tmp_path / "hours.json"
    hx.write_text(json.dumps({"tabled_from": "2026-11-07T06", "tabled_through": "2026-11-07T23", "excluded_hours": ["2026-11-07T07"], "tool": "x"}))
    out_file = tmp_path / "count.json"
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--hour-table", str(hx),
                   "--out", str(out_file)], now_ms=now) == 0
    c = json.loads(capsys.readouterr().out)
    assert c["counted_per_date"] == {"2026-11-07": 68} and c["excluded_hour_total"] == 4 and len(c["hour_table"]["sha256"]) == 64
    assert json.loads(out_file.read_text()) == c
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--out", str(out_file)], now_ms=now) == 2
    assert A.main(["day-sha", "--declog-root", str(root)], now_ms=now) == 0
    ds = json.loads(capsys.readouterr().out)["day_sha256"]
    assert sorted(ds) == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"] and all(len(v) == 64 for v in ds.values())
    assert A.main(["count", "--declog-root", "/data/mal/r4b", "--clock-ms", str(CLOCK), "--h-days", "14"], now_ms=now) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"excluded_hours": ["2026-11-07T07"]}))
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--hour-table", str(bad)], now_ms=now) == 2
    out_all = capsys.readouterr()
    assert SENTINEL_MINT not in out_all.out + out_all.err


# ---------------------------------------------------------------------------------------------------------- confirmation mode (count --from-ms)
SCREEN = ms_of("2026-11-12T00:00:00")  # a screen read instant


def test_confirmation_counts_only_attempts_at_or_after_the_screen_instant(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 13):
        b.mark(t, 1)
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-20T00:00:00"), table(), from_ms=SCREEN)
    assert out["mode"] == "confirmation" and out["from"] == "2026-11-12T00:00:00Z"
    assert out["not_counted_before_from"] == 18 + 24 * 4  # 11-07 06Z .. 11-11 23Z
    assert min(out["counted_per_date"]) == "2026-11-12"
    # 24 a day from 11-12: 11-12..11-16 = 120 on 5 dates -> boundary 2026-11-17T00:00Z (4 dates hold only 96)
    assert out["status"] == "boundary" and out["boundary"] == "2026-11-17T00:00:00Z"
    assert out["attempts_before_boundary"] == 120 and out["dates_before_boundary"] == 5


def test_confirmation_needs_five_further_dates_and_its_own_coverage(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 13):
        b.mark(t, 10)  # 240 a day: attempts never bind, the 5 further dates do
    as_of = ms_of("2026-11-20T00:00:00")
    out = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(), from_ms=SCREEN)
    assert out["boundary"] == "2026-11-17T00:00:00Z" and out["dates_before_boundary"] == 5
    # a table that stops before the confirmation's last hour withholds the boundary
    short = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(th="2026-11-16T22"), from_ms=SCREEN)
    assert short["status"] == "pending_hour_table" and short["boundary"] is None
    # coverage is judged from the screen instant's hour: a table starting there is enough
    own = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(fr="2026-11-12T00", th="2026-11-16T23"), from_ms=SCREEN)
    assert own["status"] == "boundary"
    # the screen count with the same table refuses to print (the clock's hours are not tabled)
    screen = A.count(tmp_path, ARM, CLOCK, 14, as_of, table(fr="2026-11-12T00", th="2026-11-16T23"))
    assert screen["mode"] == "screen" and screen["status"] == "pending_hour_table"


def test_confirmation_excluded_share_is_over_its_own_attempts(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 13):
        b.mark(t, 1)
    ex = [f"2026-11-08T{h:02d}" for h in range(24)] + [f"2026-11-13T{h:02d}" for h in range(6)]  # 24 screen-window hours, 6 confirmation hours
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-20T00:00:00"), table(excluded=ex), from_ms=SCREEN)
    # confirmation attempts: 11-12 24, 11-13 18 (+6 excluded), 11-14..16 72 -> 114 by 11-17T00Z on 5 dates
    assert out["boundary"] == "2026-11-17T00:00:00Z" and out["attempts_before_boundary"] == 114
    assert out["excluded_hour_before_boundary"] == 6 and out["excluded_share"] == pytest.approx(6 / 120)


def test_confirmation_end_and_refusals(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 6):
        b.mark(t, 1)
    for d in range(6, 16):
        b.mark(CLOCK // 1000 + 86400 * d, 1)  # 1 a day after 11-13: never 100 further attempts by clock + H
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-22T00:00:00"), table(), from_ms=SCREEN)
    assert out["status"] == "ended_not_decidable"
    with pytest.raises(A.Refused):
        A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-22T00:00:00"), table(), from_ms=CLOCK - 1)
    with pytest.raises(A.Refused):
        A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-22T00:00:00"), table(), from_ms=CLOCK + 14 * A.DAY_MS)


def test_cli_from_ms(tmp_path, capsys):
    root = tmp_path / "declog"
    b = Book(root)
    for t in hours("2026-11-07T06:00:00", 24 * 13):
        b.mark(t, 1)
    hx = tmp_path / "hours.json"
    hx.write_text(json.dumps({"tabled_from": "2026-11-07T06", "tabled_through": "2026-11-19T23", "excluded_hours": []}))
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--hour-table", str(hx),
                   "--from-ms", str(SCREEN)], now_ms=ms_of("2026-11-20T00:00:00")) == 0
    c = json.loads(capsys.readouterr().out)
    assert c["mode"] == "confirmation" and c["boundary"] == "2026-11-17T00:00:00Z" and c["chain_verified"] is True
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--from-ms", "5"],
                  now_ms=ms_of("2026-11-20T00:00:00")) == 2
