"""Tests for tools/r4bt_attempts.py (r4-b-T's outcome-blind attempt counter). Synthetic decision logs in tmp dirs only; no real declog is opened."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
                                      "meta": {"mint": SENTINEL_MINT, "v0": 17580000000, "mbt": t_mark_s - 7200, "s0": 1, "t_mark": 0},
                                      "stake_sol": 0.25, "decision_slot": 463000000, "t_mark": t_mark_s, "rank": r + 1, "score": 0.123456789})

    def gap(self, ms: int) -> None:
        self.log.append(ARM, ms, {"kind": "gap", "gap_kind": "tip_gap_backlog_jump", "from_ms": ms - 60000, "to_ms": ms, "data_loss": True})


def hours(start: str, n: int) -> list[int]:
    s = ms_of(start) // 1000
    return [s + 3600 * i for i in range(n)]


def test_pinned_constants():
    assert (A.GATE_ATTEMPTS, A.GATE_DATES, A.EXCLUDED_CAP, A.P0_FLOOR_MEAN) == (100, 5, 0.10, 2.0)
    assert (A.H_MIN_DAYS, A.H_MAX_DAYS, A.H_NUM) == (14, 90, 150)
    assert A.ARM_DEFAULT == "r4b-exp027"
    assert A.KEEP_KEYS == ("kind", "ms", "t_mark")


def test_r4b_live_bot_id_matches_the_default_arm():
    # the frozen code's arm name, read as text (no tools/r4b_* import from the counter or this test)
    src = (Path(__file__).resolve().parent / "r4b_live.py").read_text()
    assert 'BOT_ID = "r4b-exp027"' in src


def test_counter_imports_no_r4b_module():
    src = Path(A.__file__).read_text()
    assert "import r4b_" not in src and "from tools.r4b_" not in src and "tools import r4b" not in src


def test_h_days_formula_exact():
    assert A.h_days(309, 3) == 14  # about 103 a day
    assert A.h_days(32, 3) == 15  # rate 10.67: ceil(14.0625)
    assert A.h_days(33, 3) == 14  # rate 11: ceil(13.6)
    assert A.h_days(15, 3) == 30  # rate 5
    assert A.h_days(3, 3) == 90  # rate 1: ceil(150) capped at 90
    assert A.h_days(0, 3) is None


def test_p0_counts_entries_per_closed_day_and_nothing_else(tmp_path):
    b = Book(tmp_path)
    b.start(ms_of("2026-11-04T00:00:00") - 5000)
    for t in hours("2026-11-04T00:00:00", 72):
        b.mark(t, n_entries=4, n_exits=2)
    b.gap(ms_of("2026-11-06T23:30:00"))  # in time order, as r4b_live.log_gap keeps the log
    out = A.p0(tmp_path, ARM, ["2026-11-04", "2026-11-05", "2026-11-06"], ms_of("2026-11-07T00:30:00"))
    assert out["days"] == {"2026-11-04": 96, "2026-11-05": 96, "2026-11-06": 96}
    assert out["total"] == 288 and out["min_day"] == 96 and out["floor_ok"] is True and out["h_days"] == 14
    assert out["chain_seq"] >= 0 and len(out["chain_head"]) == 64
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


def test_the_parser_keeps_only_kind_ms_t_mark():
    row = json.loads(json.dumps({"kind": "entry", "ms": 5, "t_mark": 4, "mint": "M", "meta": {"mint": "M", "t_mark": 1}, "score": 0.9, "rank": 1}),
                     object_pairs_hook=A._keep_only)
    assert row == {"kind": "entry", "ms": 5, "t_mark": 4}


def test_slim_rows_never_materialise_other_fields(tmp_path):
    b = Book(tmp_path)
    b.mark(ms_of("2026-11-07T06:00:00") // 1000, 3)
    b.gap(ms_of("2026-11-07T06:30:00"))
    for r in A.slim_rows(tmp_path, ARM):
        assert set(r) <= set(A.KEEP_KEYS)


def test_count_boundary_is_the_first_midnight_with_100_attempts_on_5_dates(tmp_path):
    b = Book(tmp_path)
    b.start(CLOCK - 3 * 3600 * 1000)
    # 05:00Z mark decided before the clock: never counted; 06:00Z mark onward: 1 entry an hour
    b.mark(CLOCK // 1000 - 3600, 5)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    as_of = ms_of("2026-11-15T12:00:00")
    out = A.count(tmp_path, ARM, CLOCK, 14, as_of)
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
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-14T00:00:00"))
    assert out["boundary"] == "2026-11-12T00:00:00Z" and out["dates_before_boundary"] == 5


def test_count_boundary_not_reported_before_it_comes(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-11T23:59:00"))
    assert out["status"] == "pending" and out["boundary"] is None


def test_count_mark_before_clock_written_after_it_is_not_counted(tmp_path):
    b = Book(tmp_path)
    b.mark(CLOCK // 1000 - 3600, 2, decide_lag_ms=3600 * 1000 + 5000)  # a 05:00Z mark written at 06:00:05Z
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-08T00:00:00"))
    assert out["not_counted_mark_before_clock"] == 2 and out["counted_total"] == 0


def test_count_window_end_and_not_decidable(tmp_path):
    b = Book(tmp_path)
    # 1 attempt a day: never 100 by clock + 14 d
    for d in range(16):
        b.mark(CLOCK // 1000 + 86400 * d, 1)
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-22T00:00:00"))
    assert out["status"] == "ended_not_decidable" and out["boundary"] is None
    assert out["counted_total"] == 14 and out["not_counted_after_window"] == 2
    assert out["window_end"] == "2026-11-21T06:00:00Z"


def test_count_excluded_hours_leave_the_count_and_feed_the_cap(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 24 * 8):
        b.mark(t, 1)
    ex = {f"2026-11-0{d}T{h:02d}" for d in (8, 9) for h in range(0, 12)}  # 24 hours excluded
    out = A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-15T00:00:00"), ex)
    assert out["excluded_hour_total"] == 24 + 0 and out["excluded_hour_per_date"] == {"2026-11-08": 12, "2026-11-09": 12}
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


def test_broken_chain_and_torn_line_are_untrusted(tmp_path):
    b = Book(tmp_path)
    for t in hours("2026-11-07T06:00:00", 3):
        b.mark(t, 2)
    f = sorted((tmp_path / "decisions" / ARM).glob("*.jsonl"))[0]
    lines = f.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b'"rank":1', b'"rank":2')
    f.write_bytes(b"\n".join(lines))
    with pytest.raises(A.Untrusted):
        A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-08T00:00:00"))
    f.write_bytes(f.read_bytes() + b'{"kind":"entry","ms":')
    with pytest.raises(A.Untrusted):
        A.count(tmp_path, ARM, CLOCK, 14, ms_of("2026-11-08T00:00:00"), verify=False)


def test_forbidden_roots_are_refused(tmp_path):
    for p in ("/data/mal/r4b", "/data/mal/r4b/reads", "/var/lib/mal/sealed/fast-trades-tip", str(tmp_path / "tip-tape-archive" / "x")):
        with pytest.raises(A.Refused):
            A.check_root(p)
    assert A.check_root(tmp_path) == tmp_path.resolve()


def test_cli_count_p0_day_sha_and_refusals(tmp_path, capsys):
    root = tmp_path / "declog"
    b = Book(root)
    for t in hours("2026-11-04T00:00:00", 24 * 4):
        b.mark(t, 4)
    now = ms_of("2026-11-08T00:30:00")
    assert A.main(["p0", "--declog-root", str(root), "--days", "2026-11-04,2026-11-05,2026-11-06"], now_ms=now) == 0
    p0 = json.loads(capsys.readouterr().out)
    assert p0["days"] == {"2026-11-04": 96, "2026-11-05": 96, "2026-11-06": 96}
    hx = tmp_path / "hours.json"
    hx.write_text(json.dumps({"excluded_hours": ["2026-11-07T07"], "tool": "x"}))
    out_file = tmp_path / "count.json"
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--excluded-hours", str(hx),
                   "--out", str(out_file)], now_ms=now) == 0
    c = json.loads(capsys.readouterr().out)
    assert c["counted_per_date"] == {"2026-11-07": 68} and c["excluded_hour_total"] == 4 and len(c["excluded_hours_sha256"]) == 64
    assert json.loads(out_file.read_text()) == c
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--out", str(out_file)], now_ms=now) == 2
    assert A.main(["day-sha", "--declog-root", str(root)], now_ms=now) == 0
    ds = json.loads(capsys.readouterr().out)["day_sha256"]
    assert sorted(ds) == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"] and all(len(v) == 64 for v in ds.values())
    assert A.main(["count", "--declog-root", "/data/mal/r4b", "--clock-ms", str(CLOCK), "--h-days", "14"], now_ms=now) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"excluded_hours": ["2026-11-07 07"]}))
    assert A.main(["count", "--declog-root", str(root), "--clock-ms", str(CLOCK), "--h-days", "14", "--excluded-hours", str(bad)], now_ms=now) == 2
    out_all = capsys.readouterr()
    assert SENTINEL_MINT not in out_all.out + out_all.err
