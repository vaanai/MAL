"""Tests for tools/h5_boost_day_check.py: fixture hourly shadow files in a temp dir, a recording fake of subprocess.run for STOP.
No sudo, no network, no real shadow data. The clock is main()'s `now` argument; the one subprocess test that needs a clock uses MAL_BDC_NOW."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import h5_boost_day_check as bdc

SCRIPT = Path(bdc.__file__).resolve()
EXECUTOR = SCRIPT.parent / "h5_executor.py"
SENTINEL = "SENTINEL_OUTCOME_VALUE_9137"


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def pool(mint: str, sec: float | None, cls: str = "plain", sealed: bool = False, **extra) -> dict:
    """A pool-close record shaped like tools/h5_shadow.py _close(): `type` first, compact separators, outcome-ish keys present."""
    syn, src = {"plain": (False, "rpc"), "syn": (True, "ws"), "syn_rpc": (True, "rpc"), "uncl": (None, None)}[cls]
    rec = {"type": "pool", "reason": "horizon", "pool": "P" + mint, "mint": mint, "s0": 1000, "s0_block_time": 1_790_000_000,
           "boost_slices": 70, "boost_last_slice_s": sec, "boost_last_slice_s_blocktime": None, "boost_last_slice_s_recv": None,
           "min_q_pv_sol": 31.5, "min_q_fv_sol": 30.1, "triggered": ["pv"], "sealed": sealed, "synthetic": syn, "synthetic_src": src,
           "gap": False, "boost_src": "pda", "v": 1, "schema": "h5_shadow_v1", "t_ms": 1}
    rec.update(extra)
    return rec


def write_hour(d: Path, day: str, hour: int, recs: list[dict], raw_lines: list[str] = ()) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"h5-shadow-{day}T{hour:02d}.jsonl", "a") as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        for s in raw_lines:
            fh.write(s + "\n")


def fill_hours(d: Path, day: str) -> None:
    """Make all 24 hour files exist (empty where nothing was written), without touching the ones that have records."""
    d.mkdir(parents=True, exist_ok=True)
    for h in range(24):
        (d / f"h5-shadow-{day}T{h:02d}.jsonl").touch()


def many(prefix: str, n: int, sec: float, cls: str = "plain") -> list[dict]:
    return [pool(f"{prefix}{i}", sec, cls) for i in range(n)]


class FakeRun:
    def __init__(self, rc: int = 0):
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.rc)


def run(tmp_path: Path, capsys, *args: str, now: datetime, fake: FakeRun | None = None) -> tuple[int, dict]:
    fake = fake if fake is not None else FakeRun()
    argv = ["--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"), *args]
    code = bdc.main(argv, now=now, run=fake)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    rec = json.loads(out[0])
    assert rec["exit"] == code
    return code, rec


DAY = ["--mode", "day", "--day", "2026-10-11"]
N_DAY = utc("2026-10-12T00:05:00Z")
N_DAY2 = utc("2026-10-13T00:05:00Z")
DAY2 = ["--mode", "day", "--day", "2026-10-12"]


# --- day mode -----------------------------------------------------------------------------------------------------------------------
def test_day_plain_only_below_337_halts(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 3, many("p", 31, 336.0, "plain") + many("s", 40, 345.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["halt_due"] is True and rec["verdict"] == "halt_due"
    assert rec["plain"] == {"n": 31, "median_s": 336.0}
    assert rec["all"]["n"] == 71 and rec["all"]["median_s"] >= 337
    assert "plain_median_lt_337" in rec["reasons"] and "all_median_lt_337" not in rec["reasons"]
    assert rec["stop"] is None  # no --place-stop: nothing placed


def test_day_all_only_below_337_halts(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 0, many("p", 30, 338.0, "plain"))
    write_hour(sh, "2026-10-11", 23, many("s", 40, 330.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["halt_due"] is True
    assert rec["plain"]["median_s"] == 338.0 and rec["all"]["median_s"] == 330.0 and rec["syn"]["n"] == 40
    assert "all_median_lt_337" in rec["reasons"] and "plain_median_lt_337" not in rec["reasons"]
    assert rec["hours_read"] == [0, 23] and len(rec["hours_missing"]) == 22


def test_day_all_low_with_plain_under_30_still_halts(tmp_path, capsys):
    """ALL is judged on its own count: 80 pools at 290 s halt even though PLAIN (n=20) is not judged."""
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 6, many("p", 20, 290.0, "plain") + many("s", 60, 290.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["halt_due"] is True and rec["verdict"] == "halt_due"
    assert rec["all"] == {"n": 80, "median_s": 290.0} and rec["plain"]["n"] == 20
    assert rec["evaluated"] is True and rec["plain_judged"] is False
    assert "all_median_lt_337" in rec["reasons"] and "plain_median_lt_337" not in rec["reasons"]


def test_day_plain_empty_with_all_low_still_halts(tmp_path, capsys):
    """PLAIN n=0 and ALL 300 s over 100 pools: the all-only branch halts."""
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 6, many("s", 100, 300.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["halt_due"] is True
    assert rec["plain"] == {"n": 0, "median_s": None} and rec["all"] == {"n": 100, "median_s": 300.0}
    assert rec["evaluated"] is True and rec["plain_judged"] is False and "all_median_lt_337" in rec["reasons"]


def test_day_plain_under_30_and_low_is_not_judged(tmp_path, capsys):
    """PLAIN n=29 at 300 s is not judged (its own count is under 30); ALL n=70 is judged and is fine -> no halt, evaluated."""
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 6, many("p", 29, 300.0, "plain") + many("s", 41, 360.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 0 and rec["halt_due"] is False and rec["verdict"] == "no_halt"
    assert rec["evaluated"] is True and rec["plain_judged"] is False and rec["all"]["median_s"] >= 337


def test_day_both_at_or_above_337_no_halt(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 5, many("p", 30, 337.0, "plain") + many("s", 30, 350.0, "syn"))
    fill_hours(sh, "2026-10-11")  # a complete day: nothing missing, so no reasons
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 0 and rec["halt_due"] is False and rec["evaluated"] is True and rec["verdict"] == "no_halt"
    assert rec["reasons"] == [] and rec["hours_missing"] == [] and rec["plain_judged"] is True


def test_day_median_is_exact_not_rounded(tmp_path, capsys):
    # 30 plain: 15 at 336.999 and 15 at 337.000 -> median 336.9995 < 337 (a 3-decimal rounding would read 337.0)
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 1, many("a", 15, 336.999) + many("b", 15, 337.0) + many("s", 30, 360.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["plain"]["median_s"] == 336.9995


@pytest.fixture
def evaluated_1010(tmp_path):
    write_hour(tmp_path / "shadow", "2026-10-10", 7, many("e", 30, 340.0) + many("f", 30, 340.0, "syn"))


def test_day_unevaluated_then_unevaluated_halts_via_log(tmp_path, capsys, evaluated_1010):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 2, many("p", 10, 340.0) + many("s", 10, 340.0, "syn"))  # all n=20 < 30
    write_hour(sh, "2026-10-12", 2, many("q", 5, 340.0) + many("t", 20, 340.0, "syn"))
    code1, rec1 = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code1 == 5 and rec1["evaluated"] is False and rec1["halt_due"] is False and rec1["verdict"] == "unevaluated"
    assert "unevaluated_lt_30_pools" in rec1["reasons"]
    # 10-10 has no day-mode line in the log (day mode never runs on the first-strike day), so it is recomputed from its files
    assert rec1["prev_day"] == {"day": "2026-10-10", "evaluated": True, "source": "files"}
    code2, rec2 = run(tmp_path, capsys, *DAY2, now=N_DAY2)
    assert code2 == 3 and rec2["halt_due"] is True
    assert rec2["prev_day"] == {"day": "2026-10-11", "evaluated": False, "source": "log"}
    assert "two_consecutive_unevaluated_days" in rec2["reasons"]
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1]) == rec2


def test_day_evaluated_means_all_n_ge_30_for_the_previous_day_too(tmp_path, capsys):
    """10-11: PLAIN n=10 but ALL n=50 -> evaluated. A following unevaluated 10-12 is then NOT two in a row (log and files agree)."""
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 2, many("p", 10, 340.0) + many("s", 40, 345.0, "syn"))
    write_hour(sh, "2026-10-12", 2, many("q", 5, 340.0))
    code1, rec1 = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code1 == 0 and rec1["evaluated"] is True and rec1["plain_judged"] is False
    code2, rec2 = run(tmp_path, capsys, *DAY2, now=N_DAY2)  # prev from the log
    assert code2 == 5 and rec2["halt_due"] is False and rec2["prev_day"] == {"day": "2026-10-11", "evaluated": True, "source": "log"}
    (tmp_path / "log.jsonl").unlink()  # prev from the files: same answer
    code3, rec3 = run(tmp_path, capsys, *DAY2, now=N_DAY2)
    assert code3 == 5 and rec3["halt_due"] is False and rec3["prev_day"] == {"day": "2026-10-11", "evaluated": True, "source": "files"}


def test_day_log_line_evaluated_is_derived_from_all_n_not_the_stored_bool(tmp_path, capsys):
    """A v1 log line said evaluated=false when plain.n < 30 even with all.n >= 30. The reader applies the current definition to it."""
    write_hour(tmp_path / "shadow", "2026-10-12", 2, many("q", 5, 340.0))
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"tool": "h5_boost_day_check", "v": 1, "mode": "day", "day": "2026-10-11", "evaluated": False,
                               "all": {"n": 50, "median_s": 340.0}, "plain": {"n": 10, "median_s": 340.0}}) + "\n")
    code, rec = run(tmp_path, capsys, *DAY2, now=N_DAY2)
    assert code == 5 and rec["prev_day"] == {"day": "2026-10-11", "evaluated": True, "source": "log"}
    log.write_text(json.dumps({"tool": "h5_boost_day_check", "v": 1, "mode": "day", "day": "2026-10-11", "evaluated": True,
                               "all": {"n": 29, "median_s": 340.0}, "plain": {"n": 29, "median_s": 340.0}}) + "\n")
    code, rec = run(tmp_path, capsys, *DAY2, now=N_DAY2)
    assert code == 3 and rec["prev_day"] == {"day": "2026-10-11", "evaluated": False, "source": "log"}


def test_day_log_lines_written_under_a_clock_override_are_ignored(tmp_path, capsys, evaluated_1010):
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 340.0))
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": "2026-10-10", "evaluated": False, "now_overridden": True,
                               "all": {"n": 0}}) + "\n")
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 5 and rec["prev_day"] == {"day": "2026-10-10", "evaluated": True, "source": "files"}


def test_day_unevaluated_after_evaluated_prev_from_files_no_halt(tmp_path, capsys, evaluated_1010):
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 300.0))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 5 and rec["halt_due"] is False
    assert rec["prev_day"] == {"day": "2026-10-10", "evaluated": True, "source": "files"}


def test_day_unevaluated_prev_missing_from_log_and_files_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 340.0))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["prev_day"] == {"day": "2026-10-10", "evaluated": False, "source": "files"}


def test_day_log_takes_latest_line_for_prev_day(tmp_path, capsys, evaluated_1010):
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": "2026-10-10", "evaluated": True}) + "\n"
                   + "not json\n"
                   + json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": "2026-10-10", "evaluated": False}) + "\n"
                   + json.dumps({"tool": "other", "mode": "day", "day": "2026-10-10", "evaluated": True}) + "\n")
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 340.0))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["prev_day"] == {"day": "2026-10-10", "evaluated": False, "source": "log"}


def test_day_no_files_is_unevaluated_input_error(tmp_path, capsys, evaluated_1010):
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)  # 10-10 has files and is evaluated; 10-11 has none
    assert code == 2 and rec["error"] == "no_files" and rec["evaluated"] is False and rec["halt_due"] is False
    # the next day, also without files: two unevaluated days in a row -> halt, fail closed
    code2, rec2 = run(tmp_path, capsys, *DAY2, now=N_DAY2)
    assert code2 == 3 and rec2["halt_due"] is True and rec2["prev_day"]["source"] == "log"


def test_day_missing_dir_fails_closed(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["error"] == "shadow_dir_missing" and rec["evaluated"] is False  # prev (10-10) unreadable too -> unevaluated


@pytest.mark.parametrize("day,now,err", [
    ("2026-10-10", "2026-10-12T00:00:00Z", "day_not_after_first_strike_day"),
    ("2026-10-09", "2026-10-12T00:00:00Z", "day_not_after_first_strike_day"),
    ("2026-10-11", "2026-10-11T23:59:59Z", "day_not_complete"),
])
def test_day_refuses_first_strike_day_and_incomplete_day(tmp_path, capsys, day, now, err):
    write_hour(tmp_path / "shadow", day, 0, many("p", 40, 300.0))
    code, rec = run(tmp_path, capsys, "--mode", "day", "--day", day, "--place-stop", now=utc(now))
    assert code == 2 and rec["error"] == err


def test_day_requires_day(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, "--mode", "day", now=N_DAY)
    assert code == 2 and rec["error"] == "day_required"


# --- running mode -------------------------------------------------------------------------------------------------------------------
RUN = ["--mode", "running"]
N_RUN = utc("2026-10-11T12:00:00Z")


def test_running_plain_below_335_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 334.9) + many("s", 60, 345.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 3 and rec["reasons"] == ["plain_median_lt_335", "hours_missing"]


def test_running_all_below_335_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 10, 340.0) + many("s", 30, 330.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 3 and rec["reasons"] == ["all_median_lt_335", "hours_missing"]  # plain n=10 is not judged


def test_running_under_30_not_judged_and_337_is_not_a_running_halt(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 29, 300.0) + many("s", 40, 336.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and rec["halt_due"] is False and rec["all"]["median_s"] == 336.0


def test_running_day_must_be_today(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RUN, "--day", "2026-10-10", now=N_RUN)
    assert code == 2 and rec["error"] == "running_day_not_today"


def test_running_no_files_is_input_error(tmp_path, capsys):
    (tmp_path / "shadow").mkdir()
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 2 and rec["error"] == "no_files" and rec["halt_due"] is False


# --- hours_missing in day and running modes: informational only ---------------------------------------------------------------------
def test_running_hours_missing_is_a_reason_but_never_a_halt(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 4, many("p", 30, 340.0) + many("s", 30, 350.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and rec["halt_due"] is False and rec["verdict"] == "no_halt" and rec["stop"] is None
    assert rec["hours_read"] == [4] and len(rec["hours_missing"]) == 23 and rec["reasons"] == ["hours_missing"]
    fill_hours(sh, "2026-10-11")  # same records, every hour present: same verdict and exit, no reason
    code2, rec2 = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert (code2, rec2["halt_due"], rec2["verdict"]) == (code, rec["halt_due"], rec["verdict"])
    assert rec2["hours_missing"] == [] and rec2["reasons"] == []


def test_day_hours_missing_is_a_reason_but_changes_no_verdict_or_exit(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 5, many("p", 30, 337.0) + many("s", 30, 350.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 0 and rec["halt_due"] is False and rec["verdict"] == "no_halt" and rec["evaluated"] is True
    assert rec["reasons"] == ["hours_missing"] and rec["hours_missing"] == [h for h in range(24) if h != 5]
    # a halt keeps its verdict and exit 3; hours_missing comes after the halt reasons
    sh2 = tmp_path / "other" / "shadow"
    write_hour(sh2, "2026-10-11", 5, many("p", 30, 336.0) + many("s", 30, 350.0, "syn"))
    code2, rec2 = run(tmp_path / "other", capsys, *DAY, now=N_DAY)
    assert code2 == 3 and rec2["verdict"] == "halt_due" and rec2["halt_due"] is True
    assert rec2["reasons"] == ["plain_median_lt_337", "second_strike_after_first_strike_day", "hours_missing"]
    # an unevaluated day keeps exit 5 (the previous day is evaluated here, so no second-unevaluated halt)
    sh3 = tmp_path / "third" / "shadow"
    write_hour(sh3, "2026-10-10", 5, many("p", 30, 340.0))
    write_hour(sh3, "2026-10-11", 5, many("p", 10, 340.0))
    code3, rec3 = run(tmp_path / "third", capsys, *DAY, now=N_DAY)
    assert code3 == 5 and rec3["verdict"] == "unevaluated" and rec3["halt_due"] is False
    assert rec3["reasons"] == ["unevaluated_lt_30_pools", "hours_missing"]


def test_hours_missing_is_not_added_when_the_read_failed(tmp_path, capsys):
    (tmp_path / "shadow").mkdir()
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)  # no files: input error, no hours_missing key, no hours_missing reason
    assert code == 2 and rec["reasons"] == ["no_files"] and "hours_missing" not in rec


# --- resume mode --------------------------------------------------------------------------------------------------------------------
RES = ["--mode", "resume"]
N_RES = utc("2026-10-11T00:10:00Z")


def test_resume_ok(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-10", 0, many("p", 30, 337.0) + many("s", 30, 342.0, "syn"))
    fill_hours(sh, "2026-10-10")
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RES, "--place-stop", now=N_RES, fake=fake)
    assert code == 0 and rec["resume_ok"] is True and rec["verdict"] == "resume_ok" and rec["day"] == "2026-10-10"
    assert fake.calls == [] and rec["stop"] is None and rec["hours_missing"] == [] and rec["now_overridden"] is False


@pytest.mark.parametrize("recs,now,reason", [
    (many("p", 30, 336.4) + many("s", 30, 342.0, "syn"), "2026-10-11T00:10:00Z", "plain_median_lt_337"),
    (many("p", 30, 338.0) + many("s", 40, 330.0, "syn"), "2026-10-11T00:10:00Z", "all_median_lt_337"),
    (many("p", 29, 340.0) + many("s", 30, 342.0, "syn"), "2026-10-11T00:10:00Z", "plain_lt_30_pools"),
    (many("p", 30, 340.0) + many("s", 30, 342.0, "syn"), "2026-10-10T23:59:00Z", "day_not_complete"),
])
def test_resume_not_ok(tmp_path, capsys, recs, now, reason):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, recs)
    fill_hours(tmp_path / "shadow", "2026-10-10")
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, "--mode", "resume", "--place-stop", now=utc(now), fake=fake)
    assert code == 4 and rec["resume_ok"] is False and reason in rec["reasons"] and fake.calls == []


def test_resume_needs_all_24_hour_files(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-10", 0, many("p", 30, 340.0) + many("s", 30, 342.0, "syn"))
    fill_hours(sh, "2026-10-10")
    (sh / "h5-shadow-2026-10-10T13.jsonl").unlink()  # one hour file absent; everything else would pass
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 4 and rec["resume_ok"] is False and rec["hours_missing"] == [13] and rec["reasons"] == ["hours_missing"]


def test_resume_only_first_strike_day(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RES, "--day", "2026-10-09", now=N_RES)
    assert code == 2 and rec["error"] == "resume_day_not_first_strike_day"
    code, rec = run(tmp_path, capsys, *RES, "--day", "2026-10-11", now=utc("2026-10-12T01:00:00Z"))
    assert code == 2 and rec["error"] == "resume_day_not_first_strike_day"


def test_resume_no_files_is_input_error(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 2 and rec["resume_ok"] is False and rec["error"] == "shadow_dir_missing"


# --- no hidden missing data ---------------------------------------------------------------------------------------------------------
def _symlinked_hour(tmp_path: Path, day: str) -> None:
    other = tmp_path / "elsewhere"
    write_hour(other, day, 0, many("p", 40, 300.0))
    sh = tmp_path / "shadow"
    write_hour(sh, day, 1, many("q", 40, 340.0))
    (sh / f"h5-shadow-{day}T00.jsonl").symlink_to(other / f"h5-shadow-{day}T00.jsonl")


def test_symlinked_hour_file_fails_closed_in_resume(tmp_path, capsys):
    _symlinked_hour(tmp_path, "2026-10-10")
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 2 and rec["error"] == "unreadable_file" and rec["resume_ok"] is False and rec["verdict"] == "input_error"


def test_symlinked_hour_file_fails_closed_in_day(tmp_path, capsys, evaluated_1010):
    _symlinked_hour(tmp_path, "2026-10-11")
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 2 and rec["error"] == "unreadable_file" and rec["evaluated"] is False and rec["halt_due"] is False
    assert "all" not in rec  # no number from partial data


def test_unreadable_day_after_an_unevaluated_day_halts(tmp_path, capsys):
    _symlinked_hour(tmp_path, "2026-10-11")  # 10-10 has no files: unevaluated
    code, rec = run(tmp_path, capsys, *DAY, now=N_DAY)
    assert code == 3 and rec["error"] == "unreadable_file" and "two_consecutive_unevaluated_days" in rec["reasons"]


def test_symlinked_hour_file_fails_closed_in_running(tmp_path, capsys):
    _symlinked_hour(tmp_path, "2026-10-11")
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 2 and rec["error"] == "unreadable_file"


def test_hour_path_that_is_a_directory_fails_closed(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-10", 1, many("q", 40, 340.0))
    (sh / "h5-shadow-2026-10-10T00.jsonl").mkdir()
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 2 and rec["error"] == "unreadable_file"


def test_hour_file_that_cannot_be_opened_fails_closed(tmp_path, capsys, monkeypatch):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-10", 1, many("q", 40, 340.0))
    real = os.open

    def deny(path, flags, *a, **k):
        if "h5-shadow-" in str(path):
            raise PermissionError(13, "denied")
        return real(path, flags, *a, **k)

    monkeypatch.setattr(bdc.os, "open", deny)
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 2 and rec["error"] == "unreadable_file" and rec["resume_ok"] is False


def test_hour_file_that_fails_mid_read_fails_closed(tmp_path, capsys, monkeypatch):
    write_hour(tmp_path / "shadow", "2026-10-10", 1, many("q", 40, 340.0))
    real = os.fdopen

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError(5, "EIO")

    def fdopen(fd, mode="r", *a, **k):
        if mode == "rb":
            os.close(fd)
            return Boom()
        return real(fd, mode, *a, **k)

    monkeypatch.setattr(bdc.os, "fdopen", fdopen)
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 2 and rec["error"] == "unreadable_file"


# --- the executor's filter ----------------------------------------------------------------------------------------------------------
def test_executor_filter_applies_to_every_group(tmp_path, capsys):
    """reason != horizon, gap not the literal False, boost_src not pda/event_authority: dropped from ALL and from every class."""
    sh = tmp_path / "shadow"
    bad = [
        pool("r1", 100.0, reason="shutdown"), pool("r2", 100.0, "syn", reason="shutdown"),
        pool("g1", 100.0, gap=True), pool("g2", 100.0, gap=None), pool("g3", 100.0, gap=0), pool("g4", 100.0, "syn", gap=True),
        pool("b1", 100.0, boost_src="behavioural"), pool("b2", 100.0, boost_src=None), pool("b3", 100.0, "uncl", boost_src="wallet"),
        {k: v for k, v in pool("k1", 100.0).items() if k != "gap"}, {k: v for k, v in pool("k2", 100.0).items() if k != "boost_src"},
        {k: v for k, v in pool("k3", 100.0).items() if k != "reason"},
        pool("m1", 100.0, reason="shutdown", gap=True, boost_src="x"),  # fails all three conditions
    ]
    good = many("p", 30, 340.0) + many("s", 5, 341.0, "syn") + [pool("e1", 342.0, boost_src="event_authority"), pool("u1", 343.0, "uncl")]
    write_hour(sh, "2026-10-11", 4, bad + good)
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and rec["executor_filter_applied"] is True and rec["day_key"] == "file_hour"
    assert rec["plain"]["n"] == 31 and rec["syn"]["n"] == 5 and rec["other"]["n"] == 1 and rec["all"]["n"] == 37
    assert rec["skipped"]["executor_filter"] == len(bad) and rec["skipped"]["bad_sec"] == 0 and rec["pool_records"] == len(bad) + len(good)
    # reason: r1 r2 k3 m1; gap: g1 g2 g3 g4 k1 m1; boost_src: b1 b2 b3 k2 m1 (each failing condition counted on its own)
    assert rec["executor_filter_fail"] == {"reason": 4, "gap": 6, "boost_src": 5}
    assert "n_not_horizon" not in rec


def test_executor_filter_runs_before_dedupe(tmp_path, capsys):
    """A record the executor ignores does not use up its mint: the same mint's later horizon record is the one that counts."""
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 1, [pool("dup", 100.0, reason="shutdown")] + many("p", 29, 340.0))
    write_hour(sh, "2026-10-11", 2, [pool("dup", 341.0)])
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert rec["plain"]["n"] == 30 and rec["skipped"]["executor_filter"] == 1 and rec["skipped"]["dup_mint"] == 0


def test_executor_filter_ordering_matches_executor_source():
    """Pin: the filter this tool mirrors is still the one in tools/h5_executor.py, and the seconds order is the executor's."""
    src = EXECUTOR.read_text()
    assert 'row.get("reason") != "horizon" or row.get("gap") is not False or row.get("boost_src") not in ("pda", "event_authority")' in src
    assert 'for k in ("boost_last_slice_s", "boost_last_slice_s_blocktime", "boost_last_slice_s_recv"):' in src
    assert bdc.EXEC_BOOST_SRC == ("pda", "event_authority") and bdc.SEC_KEYS == ("boost_last_slice_s", "boost_last_slice_s_blocktime",
                                                                                 "boost_last_slice_s_recv")


def test_sealed_pool_record_is_counted_as_sealed_not_as_a_filter_drop(tmp_path, capsys):
    """A sealed pool's close record carries no gap/boost_src (the executor drops it too); here it is the seal guard that takes it."""
    sealed = {"type": "pool", "reason": "horizon", "sealed": True, "pool": "P", "mint": "sealedmint", "s0": 1}
    write_hour(tmp_path / "shadow", "2026-10-11", 1, [sealed] + many("p", 30, 340.0))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert rec["skipped"]["sealed"] == 1 and rec["skipped"]["executor_filter"] == 0 and rec["plain"]["n"] == 30


# --- reading: allowlist, dedupe, sealed, seconds ------------------------------------------------------------------------------------
def test_field_allowlist_drops_outcome_like_keys():
    raw = json.dumps(pool("m1", 340.0, pnl_sol=SENTINEL, exit_px=SENTINEL, fills=[{"sol": SENTINEL}], nested={"type": "pool", "mint": "x"}),
                     separators=(",", ":")).encode()
    rec = bdc.parse_pool_line(raw)
    assert set(rec) <= bdc.ALLOWED_KEYS
    assert "min_q_pv_sol" not in rec and "triggered" not in rec and "gaps" not in rec and "boost_pda" not in rec
    assert rec["gap"] is False and rec["boost_src"] == "pda"  # structure fields the executor's filter reads
    assert SENTINEL not in json.dumps(rec)


def test_allowlist_has_no_outcome_trigger_or_pnl_name():
    assert not [k for k in bdc.ALLOWED_KEYS if any(w in k for w in ("pnl", "min_q", "trigger", "fill", "exit", "profit", "outcome", "sol"))]


def test_non_pool_records_are_counted_never_parsed_and_never_printed(tmp_path, capsys, monkeypatch):
    parsed: list[bytes] = []
    real = bdc.parse_pool_line

    def spy(raw):
        parsed.append(raw)
        return real(raw)

    monkeypatch.setattr(bdc, "parse_pool_line", spy)
    outcome = {"type": "outcome", "mint": "o1", "pnl_sol": SENTINEL, "boost_last_slice_s": 1.0, "sealed": False}
    trigger = {"type": "trigger", "mint": "t1", "q_trigger_sol": SENTINEL, "boost_last_slice_s": 1.0}
    excluded = {"type": "excluded", "mint": "x1", "reason": "synthetic", "boost_last_slice_s": 1.0}
    pools = many("p", 30, 340.0) + [pool("q", 341.0, pnl_sol=SENTINEL)]
    lines = [json.dumps(r, separators=(",", ":")) for r in (outcome, trigger, excluded, outcome)]
    # a pool line whose `type` is not the leading key is still parsed (through the allowlist hook)
    late_type = json.dumps({"mint": "late", "pnl_sol": SENTINEL, "type": "pool", "sealed": False, "synthetic": False, "reason": "horizon",
                            "gap": False, "boost_src": "pda", "synthetic_src": "rpc", "boost_last_slice_s": 342.0}, separators=(",", ":"))
    write_hour(tmp_path / "shadow", "2026-10-11", 9, pools, raw_lines=lines + [late_type, "{broken"])
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert rec["lines_by_type"] == {"pool": 32, "other": 4}  # outcome x2, trigger, excluded: counted together, never by type
    assert rec["plain"]["n"] == 32 and rec["all"]["n"] == 32 and rec["skipped"]["bad_line"] == 1
    assert all(b'"type":"pool"' in r or r.startswith(b"{broken") for r in parsed) and len(parsed) == 33
    blob = (tmp_path / "log.jsonl").read_text()
    assert SENTINEL not in blob and "o1" not in blob and "t1" not in blob and "x1" not in blob
    assert "pnl" not in blob and "q_trigger" not in blob and "min_q" not in blob
    assert all(f'"{w}"' not in blob for w in ("outcome", "trigger", "excluded", "strip"))  # no per-type name is printed


def test_lines_by_type_is_only_pool_and_other(tmp_path, capsys):
    """Seal hygiene: the only per-type output is {"pool", "other"}. Any non-pool line, however its type is spelled or placed, is "other"."""
    sh = tmp_path / "shadow"
    typeless = json.dumps({"mint": "nt", "sealed": False, "boost_last_slice_s": 340.0}, separators=(",", ":"))
    odd_type = json.dumps({"mint": "ot", "type": 7, "sealed": False}, separators=(",", ":"))
    lead_other = json.dumps({"type": "strip", "mint": "st"}, separators=(",", ":"))
    write_hour(sh, "2026-10-11", 1, many("p", 30, 340.0), raw_lines=[typeless, odd_type, lead_other, "{broken", "   "])
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert rec["lines_by_type"] == {"pool": 30, "other": 3} and list(rec["lines_by_type"]) == ["pool", "other"]
    assert rec["skipped"]["bad_line"] == 1 and rec["pool_records"] == 30 and rec["plain"]["n"] == 30
    assert rec["lines_by_type"]["pool"] == rec["pool_records"]


def test_output_names_no_pool_or_mint(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 0, many("MINTNAME", 30, 340.0))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert "MINTNAME" not in json.dumps(rec) and "PMINTNAME" not in json.dumps(rec)


def test_dedupe_by_mint_first_valid_record(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 1, [pool("dup", 2500.0)] + many("p", 29, 340.0))  # first record of dup: bad seconds, not kept
    write_hour(sh, "2026-10-11", 2, [pool("dup", 300.0), pool("dup", 400.0, "syn")])
    write_hour(sh, "2026-10-11", 3, [pool("dup", 500.0)])
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert rec["plain"]["n"] == 30 and rec["all"]["n"] == 30 and rec["syn"]["n"] == 0
    assert rec["skipped"]["dup_mint"] == 2 and rec["skipped"]["bad_sec"] == 1


def test_sealed_and_no_mint_skipped(tmp_path, capsys):
    sh = tmp_path / "shadow"
    sealed = [pool(f"z{i}", 100.0, sealed=True) for i in range(40)]
    sealed_missing = [{k: v for k, v in pool("y", 100.0).items() if k != "sealed"}]
    write_hour(sh, "2026-10-11", 1, sealed + sealed_missing + [pool("", 100.0), {**pool("n", 100.0), "mint": None}] + many("p", 30, 340.0))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and rec["plain"] == {"n": 30, "median_s": 340.0}
    assert rec["skipped"]["sealed"] == 41 and rec["skipped"]["no_mint"] == 2 and rec["pool_records"] == 73


def test_seconds_choice_mirrors_executor():
    p = bdc.pick_seconds
    assert p({"boost_last_slice_s": 338.12345}) == 338.123
    assert p({"boost_last_slice_s": None, "boost_last_slice_s_blocktime": 339, "boost_last_slice_s_recv": 1.0}) == 339.0
    assert p({"boost_last_slice_s": True, "boost_last_slice_s_blocktime": None, "boost_last_slice_s_recv": 341.5}) == 341.5  # bool skipped
    assert p({"boost_last_slice_s": 0, "boost_last_slice_s_blocktime": 339}) is None  # first number out of range: no fall-through
    assert p({"boost_last_slice_s": 2000.0}) is None and p({"boost_last_slice_s": float("nan")}) is None
    assert p({"boost_last_slice_s": "340"}) is None and p({}) is None


def test_seconds_too_large_for_a_float_is_none_not_a_crash():
    """float(10**400) raises OverflowError; pick_seconds turns that into None (a bad value), and does not fall through to the next key."""
    p = bdc.pick_seconds
    assert p({"boost_last_slice_s": 10**400}) is None and p({"boost_last_slice_s": -(10**400)}) is None
    assert p({"boost_last_slice_s": 10**400, "boost_last_slice_s_blocktime": 339.0}) is None  # first number is bad: no fall-through
    assert p({"boost_last_slice_s": None, "boost_last_slice_s_blocktime": 10**400, "boost_last_slice_s_recv": 341.0}) is None


def test_huge_int_seconds_in_a_file_is_counted_bad_sec_and_does_not_crash(tmp_path, capsys):
    raw = '{"type":"pool","reason":"horizon","sealed":false,"mint":"hugeraw","synthetic":false,"synthetic_src":"rpc","gap":false,' \
          '"boost_src":"pda","boost_last_slice_s":1' + "0" * 400 + "}"  # the literal integer 10**400 as it would sit in a shadow file
    write_hour(tmp_path / "shadow", "2026-10-11", 2, [pool("big", 10**400), pool("neg", -(10**400)), pool("big2", 10**400,
               boost_last_slice_s_blocktime=339.0)] + many("p", 30, 340.0), raw_lines=[raw])
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and "error" not in rec
    assert rec["skipped"]["bad_sec"] == 4 and rec["skipped"]["bad_line"] == 0 and rec["pool_records"] == 34
    assert rec["plain"] == {"n": 30, "median_s": 340.0} and rec["all"]["n"] == 30 and rec["halt_due"] is False


def test_classes():
    assert bdc.pool_class({"synthetic": False, "synthetic_src": "rpc"}) == "plain"
    assert bdc.pool_class({"synthetic": False, "synthetic_src": "ws"}) == "other"
    assert bdc.pool_class({"synthetic": 0, "synthetic_src": "rpc"}) == "other"
    assert bdc.pool_class({"synthetic": True, "synthetic_src": "rpc"}) == "syn"
    assert bdc.pool_class({}) == "other"


# --- STOP --------------------------------------------------------------------------------------------------------------------------
def test_place_stop_not_invoked_without_flag(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", boom)
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    argv = ["--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"), *RUN]
    assert bdc.main(argv, now=N_RUN) == 3  # the default runner is subprocess.run, looked up at call time
    capsys.readouterr()
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN, fake=fake)
    assert code == 3 and fake.calls == [] and rec["stop"] is None and rec["place_stop_flag"] is False


def test_place_stop_with_flag_touches_then_tests_never_removes(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", now=N_RUN, fake=fake)
    assert code == 3
    assert fake.calls == [["sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"], ["sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP"]]
    assert rec["stop"] == {"path": "/var/lib/mal-live/h5/STOP", "touch_rc": 0, "test_rc": 0, "present": True}


def test_place_stop_failure_is_logged_and_exits_6(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", now=N_RUN, fake=FakeRun(rc=1))
    assert code == 6 and rec["halt_due"] is True and rec["stop"]["present"] is False and rec["stop"]["touch_rc"] == 1


def test_place_stop_touch_ok_but_not_verified_exits_6(tmp_path, capsys):
    class TouchOnly(FakeRun):
        def __call__(self, cmd, **kw):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1 if "test" in cmd else 0)

    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", now=N_RUN, fake=TouchOnly())
    assert code == 6 and rec["stop"] == {"path": "/var/lib/mal-live/h5/STOP", "touch_rc": 0, "test_rc": 1, "present": False}


def test_place_stop_oserror_exits_6(tmp_path, capsys):
    def broken(cmd, **kw):
        raise FileNotFoundError("sudo")

    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", now=N_RUN, fake=broken)
    assert code == 6 and rec["stop"]["present"] is False and rec["stop"]["touch_error"] == "FileNotFoundError"


def test_day_halt_with_stop_unverified_exits_6_and_verified_exits_3(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("s", 100, 300.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY, "--place-stop", now=N_DAY, fake=FakeRun(rc=1))
    assert code == 6 and rec["verdict"] == "halt_due"
    code, rec = run(tmp_path, capsys, *DAY, "--place-stop", now=N_DAY, fake=FakeRun(rc=0))
    assert code == 3 and rec["stop"]["present"] is True


def test_place_stop_flag_without_halt_does_nothing(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 340.0))
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", now=N_RUN, fake=fake)
    assert code == 0 and fake.calls == []


def test_source_never_removes_stop():
    src = SCRIPT.read_text()
    assert '"rm"' not in src and "unlink" not in src and "os.remove" not in src and "rmtree" not in src


# --- exit codes, flags, clock -------------------------------------------------------------------------------------------------------
def test_exit_codes_are_distinct_per_outcome(tmp_path, capsys, evaluated_1010):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 2, many("p", 40, 340.0))                    # evaluated, fine
    write_hour(sh, "2026-10-12", 2, many("q", 5, 340.0))                     # unevaluated, previous evaluated
    write_hour(sh, "2026-10-13", 2, many("s", 60, 300.0, "syn"))             # all low
    assert run(tmp_path, capsys, *DAY, now=N_DAY)[0] == 0
    assert run(tmp_path, capsys, *DAY2, now=N_DAY2)[0] == 5
    assert run(tmp_path, capsys, "--mode", "day", "--day", "2026-10-13", now=utc("2026-10-14T00:05:00Z"))[0] == 3
    assert run(tmp_path, capsys, "--mode", "day", "--day", "2026-10-13", "--place-stop", now=utc("2026-10-14T00:05:00Z"), fake=FakeRun(rc=1))[0] == 6
    assert run(tmp_path, capsys, *RES, now=N_RES)[0] == 4     # 10-10 has one hour file only
    assert run(tmp_path, capsys, "--mode", "day", now=N_DAY)[0] == 2


@pytest.mark.parametrize("flag", [["--now-utc", "2026-10-11T00:10:00Z"], ["--first-strike-day", "2026-10-08"]])
def test_cli_has_no_test_only_clock_or_strike_flag(tmp_path, capsys, flag):
    with pytest.raises(SystemExit) as ei:
        bdc.main(["--shadow-dir", str(tmp_path), "--log", str(tmp_path / "l.jsonl"), "--mode", "day", "--day", "2026-10-11", *flag])
    assert ei.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_first_strike_day_is_a_pinned_constant():
    assert bdc.FIRST_STRIKE_DAY == "2026-10-10"
    assert "first-strike" not in SCRIPT.read_text().split("def build_parser", 1)[1].split("def _public", 1)[0]


def test_main_now_argument_is_not_an_override(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, many("p", 30, 338.0))
    fill_hours(tmp_path / "shadow", "2026-10-10")
    code, rec = run(tmp_path, capsys, *RES, now=N_RES)
    assert code == 0 and rec["now_overridden"] is False and rec["now_utc"] == "2026-10-11T00:10:00Z"


def test_env_clock_override_is_stamped_and_blocks_resume_ok(tmp_path, capsys, monkeypatch):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, many("p", 30, 338.0) + many("s", 30, 342.0, "syn"))
    fill_hours(tmp_path / "shadow", "2026-10-10")
    monkeypatch.setenv("MAL_BDC_NOW", "2026-10-11T00:10:00Z")
    argv = ["--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"), *RES]
    code = bdc.main(argv)
    rec = json.loads(capsys.readouterr().out)
    assert code == 4 and rec["now_overridden"] is True and rec["now_utc"] == "2026-10-11T00:10:00Z"
    assert rec["resume_ok"] is False and rec["reasons"] == ["now_overridden"] and rec["verdict"] == "resume_not_ok"


def test_env_clock_override_bad_value_is_input_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MAL_BDC_NOW", "not-a-time")
    code = bdc.main(["--shadow-dir", str(tmp_path), "--log", str(tmp_path / "log.jsonl"), *RES])
    rec = json.loads(capsys.readouterr().out)
    assert code == 2 and rec["error"] == "bad_now_env"


def test_real_clock_when_no_override(monkeypatch):
    monkeypatch.delenv("MAL_BDC_NOW", raising=False)
    dt, overridden = bdc.resolve_now(None)
    assert overridden is False and abs((datetime.now(timezone.utc) - dt).total_seconds()) < 5


# --- log and process ---------------------------------------------------------------------------------------------------------------
def test_log_path_under_structure_monitor_refused(tmp_path, capsys):
    code = bdc.main(["--shadow-dir", str(tmp_path), "--log", "/data/mal/structure-monitor/x.jsonl", *RUN], now=N_RUN, run=FakeRun())
    assert code == 2 and json.loads(capsys.readouterr().out)["error"] == "log_path_forbidden"


def test_log_path_symlinked_into_structure_monitor_refused(tmp_path, capsys, monkeypatch):
    real = bdc.os.path.realpath
    monkeypatch.setattr(bdc.os.path, "realpath", lambda p: "/data/mal/structure-monitor/daily.jsonl" if p.endswith("evil.jsonl") else real(p))
    code = bdc.main(["--shadow-dir", str(tmp_path), "--log", str(tmp_path / "evil.jsonl"), *RUN], now=N_RUN, run=FakeRun())
    assert code == 2 and not (tmp_path / "evil.jsonl").exists()


def test_running_reports_which_groups_were_judged(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 29, 340.0) + many("s", 30, 340.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN, now=N_RUN)
    assert code == 0 and rec["judged"] == {"plain": False, "all": True} and rec["plain_judged"] is False


def test_log_appends_one_line_per_run(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 340.0))
    for _ in range(3):
        run(tmp_path, capsys, *RUN, now=N_RUN)
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(x)["tool"] == "h5_boost_day_check" for x in lines)


def _sub(tmp_path: Path, *args: str, now: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "MAL_BDC_NOW"}
    if now is not None:
        env["MAL_BDC_NOW"] = now
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"),
                           *args], capture_output=True, text=True, cwd=str(tmp_path), timeout=60, stdin=subprocess.DEVNULL, env=env)


def test_runs_isolated_stdlib_only_day_mode_with_env_clock(tmp_path):
    write_hour(tmp_path / "shadow", "2026-10-11", 0, many("p", 30, 338.0) + many("s", 30, 342.0, "syn"))
    p = _sub(tmp_path, *DAY, now="2026-10-12T00:01:00Z")
    assert p.returncode == 0, p.stderr
    rec = json.loads(p.stdout)
    assert rec["verdict"] == "no_halt" and rec["plain"] == {"n": 30, "median_s": 338.0} and rec["all"]["median_s"] == 340.0
    assert rec["now_overridden"] is True and rec["executor_filter_applied"] is True and rec["day_key"] == "file_hour"


def test_runs_isolated_resume_with_env_clock_is_never_ok(tmp_path):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, many("p", 30, 338.0) + many("s", 30, 342.0, "syn"))
    fill_hours(tmp_path / "shadow", "2026-10-10")
    p = _sub(tmp_path, *RES, now="2026-10-11T00:01:00Z")
    assert p.returncode == 4, p.stderr
    rec = json.loads(p.stdout)
    assert rec["resume_ok"] is False and rec["now_overridden"] is True and rec["reasons"] == ["now_overridden"]


def test_runs_isolated_rejects_removed_flags(tmp_path):
    p = _sub(tmp_path, *RES, "--now-utc", "2026-10-11T00:01:00Z", now=None)
    assert p.returncode == 2 and "unrecognized arguments" in p.stderr and p.stdout == ""
