"""tools/h5_exec_day_check.py: the executor's own completed-day median (DEC-024 Am.4 item 2, Am.5 item 6), and the `decided` query.

Every privileged call goes through a fake `run`: no sudo, no /var/lib/mal-live. The counters are synthetic and carry sentinel outcome
fields that must never reach the output.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import h5_exec_day_check as x

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 12, 0, 21, tzinfo=timezone.utc)
SENTINEL = 987654321


UNIT_UP = {"LoadState": "loaded", "ActiveState": "active", "ActiveEnterTimestamp": "Thu 2026-10-01 09:00:00 UTC",
           "ActiveExitTimestamp": "", "InactiveEnterTimestamp": "", "InactiveExitTimestamp": "Thu 2026-10-01 08:59:59 UTC"}


def unit_text(props: dict) -> bytes:
    return "".join(f"{k}={v}\n" for k, v in props.items()).encode()


class FakeRun:
    def __init__(self, counters: object = None, dd_rc: int = 0, raw: bytes | None = None, test_rc: int = 0,
                 unit: dict | None = None, unit_rc: int = 0) -> None:
        self.raw = raw if raw is not None else json.dumps(counters).encode()
        self.dd_rc, self.test_rc = dd_rc, test_rc
        self.unit, self.unit_rc = (UNIT_UP if unit is None else unit), unit_rc
        self.calls: list[tuple[str, ...]] = []
        self.unit_kw: list[dict] = []

    def __call__(self, argv, **kw):
        self.calls.append(tuple(argv))
        if argv[:1] == ["/usr/bin/systemctl"]:
            assert tuple(argv) == x.SYSTEMCTL_ARGV and kw["env"]["TZ"] == "UTC"
            self.unit_kw.append(kw)
            return SimpleNamespace(returncode=self.unit_rc, stdout=unit_text(self.unit) if self.unit_rc == 0 else b"")
        if "/usr/bin/dd" in argv:
            return SimpleNamespace(returncode=self.dd_rc, stdout=self.raw if self.dd_rc == 0 else b"")
        if argv[:3] == ["sudo", "-n", "touch"]:
            return SimpleNamespace(returncode=0)
        if argv[:3] == ["sudo", "-n", "test"]:
            return SimpleNamespace(returncode=self.test_rc)
        raise AssertionError(argv)


def counters(day: str = "2026-10-11", median: float | None = 336.5, pools: int | None = 40) -> dict:
    entry: dict = {"trades": SENTINEL, "realized": SENTINEL, "boost_lt300": [], "boost_median": median}
    if pools is not None:
        entry["boost_s"] = {f"mint{i}": 330.0 + i for i in range(pools)}
    return {"schema": "h5_counters_v1", "run_mode": "live", "days": {day: entry, "2026-10-12": {"trades": SENTINEL, "realized": SENTINEL}},
            "halts": {"SENTINELHALT": {"realized": SENTINEL}}, "plans": {"p": {"fill": SENTINEL}}}


def run_day(tmp_path: Path, fake: FakeRun, day: str = "2026-10-11", place: bool = True, now: datetime = NOW) -> tuple[int, dict, str]:
    log = tmp_path / "exec-day.jsonl"
    argv = ["--mode", "day", "--day", day, "--log", str(log)] + (["--place-stop"] if place else [])
    rc = x.main(argv, now=now, run=fake)
    text = log.read_text()
    return rc, json.loads(text.splitlines()[-1]), text


def stop_calls(fake: FakeRun) -> list[tuple[str, ...]]:
    return [c for c in fake.calls if c[:3] in (("sudo", "-n", "touch"), ("sudo", "-n", "test"))]


def unit_calls(fake: FakeRun) -> int:
    return sum(1 for c in fake.calls if c[:1] == ("/usr/bin/systemctl",))


# ---- DEC-024 Am.4 item 2: "two consecutive unevaluated days halt", on the executor's measure ----

NOW_13 = datetime(2026, 10, 13, 0, 21, tzinfo=timezone.utc)


def days_counters(**days: dict | None) -> dict:
    """days_counters(d11={...} or None, d12=...): a day given as None has no entry at all."""
    out = {}
    for k, v in days.items():
        if v is not None:
            out["2026-10-" + k[1:]] = {"trades": SENTINEL, "realized": SENTINEL, **v}
    return {"schema": "h5_counters_v1", "run_mode": "live", "days": out, "halts": {"SENTINELHALT": {"realized": SENTINEL}}}


def pools(n: int, median: float | None) -> dict:
    return {"boost_s": {f"m{i}": 340.0 for i in range(n)}, "boost_median": median}


@pytest.mark.parametrize("d11,d12", [
    (pools(5, None), pools(5, None)),        # the reviewer's fake run: 5 pools and no median on both days
    (None, None),                            # no day entry at all, executor up (stuck tail)
    (pools(5, None), None),
    (None, pools(29, 336.0)),                # a median on fewer than 30 pools does not count
])
def test_two_consecutive_unevaluated_days_place_stop(tmp_path: Path, d11, d12) -> None:
    fake = FakeRun(days_counters(d11=d11, d12=d12))
    rc, rec, text = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert rc == 3 and rec["verdict"] == "halt_due" and rec["halt_due"] is True
    assert rec["reasons"] == ["two_consecutive_unevaluated_days"]  # distinct from executor_median_lt_337 (DEC-028 s3.5)
    assert rec["prev_day"]["day"] == "2026-10-11" and rec["prev_day"]["status"] == "unevaluated"
    assert stop_calls(fake) == [("sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"), ("sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP")]
    assert rec["stop"]["present"] is True
    assert unit_calls(fake) <= 1 and str(SENTINEL) not in text and "SENTINELHALT" not in text


def test_two_unevaluated_days_with_stop_not_verified_is_exit_6(tmp_path: Path) -> None:
    rc, rec, _ = run_day(tmp_path, FakeRun(days_counters(d11=None, d12=None), test_rc=1), day="2026-10-12", now=NOW_13)
    assert rc == 6 and rec["halt_due"] is True and rec["stop"]["present"] is False


def test_two_unevaluated_days_without_place_stop_is_exit_3_untouched(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d11=None, d12=None))
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13, place=False)
    assert rc == 3 and rec["stop"] is None and stop_calls(fake) == []


@pytest.mark.parametrize("d11,d12,day,now,why", [
    (pools(40, 339.0), pools(5, None), "2026-10-12", NOW_13, "prev_evaluated"),
    (pools(5, None), pools(40, 339.0), "2026-10-12", NOW_13, "day_evaluated"),
    (pools(5, None), None, "2026-10-11", NOW, "prev_is_first_strike_day"),  # 10-10 is not a day of this rule
])
def test_one_unevaluated_day_does_not_halt(tmp_path: Path, d11, d12, day, now, why) -> None:
    fake = FakeRun(days_counters(d11=d11, d12=d12))
    rc, rec, _ = run_day(tmp_path, fake, day=day, now=now)
    assert stop_calls(fake) == [] and rec["halt_due"] is False
    if why == "day_evaluated":
        assert rc == 0 and rec["verdict"] == "no_halt" and "prev_day" not in rec
    else:
        assert rc == 5 and rec["verdict"] == "unevaluated"
    if why == "prev_is_first_strike_day":
        assert "prev_day" not in rec


UNIT_DOWN_1011 = {"LoadState": "loaded", "ActiveState": "active", "ActiveEnterTimestamp": "Mon 2026-10-12 10:00:00 UTC",
                  "ActiveExitTimestamp": "Sat 2026-10-10 21:00:00 UTC", "InactiveEnterTimestamp": "Sat 2026-10-10 21:00:01 UTC",
                  "InactiveExitTimestamp": "Mon 2026-10-12 09:59:59 UTC"}


def test_no_entry_while_the_unit_was_down_all_day_is_unevaluated(tmp_path: Path) -> None:
    """Stopped 10-10T21Z, started 10-12T10Z with 12 pools. DEC-024 Am.4 item 2, literal (manager's decision, PR #593 round 15): 10-11 has
    0 pools on the executor's count, so it is unevaluated whatever the unit state, and with 10-12 it is a pair: halt. The unit state is
    only reported (executor_ran)."""
    fake = FakeRun(days_counters(d11=None, d12=pools(12, None)), unit=UNIT_DOWN_1011)
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert rc == 3 and rec["reasons"] == ["two_consecutive_unevaluated_days"] and rec["stop"]["present"] is True
    assert stop_calls(fake) == [("sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"), ("sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP")]
    assert rec["prev_day"]["status"] == "unevaluated" and rec["prev_day"]["why"] == "no_executor_day_entry"
    assert rec["prev_day"]["executor_ran"] == "no"  # reported, never a reason to spare the day
    assert rec["unit"]["ActiveEnterTimestamp"] == "Mon 2026-10-12 10:00:00 UTC"


UNIT_DOWN_1212 = {"LoadState": "loaded", "ActiveState": "inactive", "ActiveEnterTimestamp": "Thu 2026-10-01 09:00:00 UTC",
                  "ActiveExitTimestamp": "Sun 2026-10-11 23:00:00 UTC", "InactiveEnterTimestamp": "Sun 2026-10-11 23:00:01 UTC",
                  "InactiveExitTimestamp": "Thu 2026-10-01 08:59:59 UTC"}


def test_a_day_without_entry_while_down_pairs_with_an_unevaluated_day_before(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d11=pools(5, None), d12=None), unit=UNIT_DOWN_1212)
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert rc == 3 and rec["day_status"] == "unevaluated" and rec["reasons"] == ["two_consecutive_unevaluated_days"]
    assert rec["executor_ran"] == "no" and rec["ran_why"] == "unit_inactive_all_day" and rec["stop"]["present"] is True
    assert unit_calls(fake) == 1


def test_a_day_without_entry_while_down_alone_is_exit_5(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d11=pools(40, 339.0), d12=None), unit=UNIT_DOWN_1212)
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert rc == 5 and rec["day_status"] == "unevaluated" and rec["reasons"] == ["no_executor_day_entry"] and stop_calls(fake) == []
    assert rec["prev_day"]["status"] == "evaluated" and rec["executor_ran"] == "no"


def _ent(n: int, med: float | None = None) -> dict:
    return {"boost_lt300": [], "boost_median": med, "boost_s": {f"m{i}": 330.0 for i in range(n)}}


# quant-proof's round-2 probes (PR #593), as tests: the unit was down; each case halts under the literal Am.4 item 2.
DOWN_1213 = {"LoadState": "loaded", "ActiveState": "active", "ActiveEnterTimestamp": "Wed 2026-10-14 20:00:00 UTC",
             "ActiveExitTimestamp": "Mon 2026-10-12 22:00:00 UTC", "InactiveEnterTimestamp": "Mon 2026-10-12 22:00:01 UTC",
             "InactiveExitTimestamp": "Wed 2026-10-14 19:59:59 UTC"}


def test_probe_5_pools_then_unit_down_with_no_entry_halts_at_the_next_check(tmp_path: Path) -> None:
    """10-11 evaluated (338 s over 500), 10-12 5 pools, 10-13 the unit down all day (no entry), 10-14 8 pools after a 20:00Z restart.
    The 10-13T00:21Z check (day 10-12) is exit 5; the 10-14T00:21Z check (day 10-13) halts (rc 3, STOP placed); so does 10-15's."""
    c = days_counters(d11=_ent(500, 338.0), d12=_ent(5), d14=_ent(8))
    got = []
    for day, now in (("2026-10-12", datetime(2026, 10, 13, 0, 21, tzinfo=timezone.utc)),
                     ("2026-10-13", datetime(2026, 10, 14, 0, 21, tzinfo=timezone.utc)),
                     ("2026-10-14", datetime(2026, 10, 15, 0, 21, tzinfo=timezone.utc))):
        fake = FakeRun(c, unit=DOWN_1213)
        rc, rec, _ = run_day(tmp_path, fake, day=day, now=now)
        got.append((rc, rec["verdict"], rec["reasons"], len(stop_calls(fake))))
        if day == "2026-10-13":
            assert rec["executor_ran"] == "no" and rec["prev_day"]["status"] == "unevaluated" and rec["stop"]["present"] is True
    assert got == [(5, "unevaluated", ["unevaluated_lt_30_pools"], 0),
                   (3, "halt_due", ["two_consecutive_unevaluated_days"], 2),
                   (3, "halt_due", ["two_consecutive_unevaluated_days"], 2)]


def test_probe_unit_down_then_9_pools_halts(tmp_path: Path) -> None:
    """10-11 evaluated, 10-12 the unit down all day (no entry), 10-13 9 pools after an 18:00Z restart: the 10-14T00:21Z check halts."""
    unit = dict(DOWN_1213, ActiveEnterTimestamp="Tue 2026-10-13 18:00:00 UTC", ActiveExitTimestamp="Sun 2026-10-11 22:00:00 UTC",
                InactiveEnterTimestamp="Sun 2026-10-11 22:00:01 UTC", InactiveExitTimestamp="Tue 2026-10-13 17:59:59 UTC")
    fake = FakeRun(days_counters(d11=_ent(500, 338.0), d13=_ent(9)), unit=unit)
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-13", now=datetime(2026, 10, 14, 0, 21, tzinfo=timezone.utc))
    assert rc == 3 and rec["verdict"] == "halt_due" and rec["reasons"] == ["two_consecutive_unevaluated_days"]
    assert rec["prev_day"]["status"] == "unevaluated" and rec["prev_day"]["executor_ran"] == "no" and rec["stop"]["present"] is True


def test_classify_has_no_not_running_status() -> None:
    src = (ROOT / "tools" / "h5_exec_day_check.py").read_text()
    assert "not_running" not in src and "executor_not_running_on_day" not in src


@pytest.mark.parametrize("unit,unit_rc", [({}, 1), ({"LoadState": "not-found", "ActiveState": "inactive"}, 0),
                                          ({"LoadState": "loaded", "ActiveState": "active", "ActiveEnterTimestamp": "Tue 2026-10-13 00:05:00 UTC",
                                            "InactiveEnterTimestamp": "Tue 2026-10-13 00:04:00 UTC"}, 0)])
def test_unit_doubt_counts_as_running(tmp_path: Path, unit, unit_rc) -> None:
    """Unit unreadable, not loaded, or restarted after the day with no history covering it: the doubt errs toward STOP."""
    fake = FakeRun(days_counters(d11=None, d12=None), unit=unit, unit_rc=unit_rc)
    rc, rec, _ = run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert rc == 3 and rec["reasons"] == ["two_consecutive_unevaluated_days"] and rec["executor_ran"] == "unknown"
    assert unit_calls(fake) == 1  # one systemctl call per run, however many days need it


def test_systemctl_is_only_called_for_a_day_without_entry_and_never_with_sudo(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d11=pools(5, None), d12=pools(5, None)))
    run_day(tmp_path, fake, day="2026-10-12", now=NOW_13)
    assert unit_calls(fake) == 0
    assert x.SYSTEMCTL_ARGV[:3] == ("/usr/bin/systemctl", "show", "--no-pager") and "sudo" not in x.SYSTEMCTL_ARGV


@pytest.mark.parametrize("props,want", [
    (UNIT_UP, "yes"),
    (UNIT_DOWN_1011, "no"),
    ({"LoadState": "loaded", "ActiveState": "failed", "ActiveEnterTimestamp": "Thu 2026-10-01 09:00:00 UTC",
      "ActiveExitTimestamp": "Sun 2026-10-11 06:00:00 UTC", "InactiveEnterTimestamp": "Sun 2026-10-11 06:00:01 UTC"}, "yes"),  # up part of the day
    ({"LoadState": "loaded", "ActiveState": "failed", "ActiveEnterTimestamp": "Thu 2026-10-01 09:00:00 UTC",
      "ActiveExitTimestamp": "Sat 2026-10-10 06:00:00 UTC", "InactiveEnterTimestamp": "Sat 2026-10-10 06:00:01 UTC"}, "no"),
    ({"LoadState": "loaded", "ActiveState": "active", "ActiveEnterTimestamp": "n/a"}, "unknown"),
    (None, "unknown"),
])
def test_ran_on(props, want) -> None:
    assert x.ran_on(props, datetime(2026, 10, 11).date(), NOW_13.timestamp())[0] == want


# ---- running mode: DEC-024 Am.5 item 6, the executor's running median below 335 s over >= 30 pools places STOP ----

AT_0547 = datetime(2026, 10, 12, 5, 47, tzinfo=timezone.utc)


def run_running(tmp_path: Path, fake: FakeRun, place: bool = True) -> tuple[int, dict]:
    log = tmp_path / "exec-day.jsonl"
    rc = x.main(["--mode", "running", "--day", "2026-10-12", "--log", str(log)] + (["--place-stop"] if place else []), now=AT_0547, run=fake)
    return rc, json.loads(log.read_text().splitlines()[-1])


def test_running_median_below_335_over_30_pools_places_stop(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d12=pools(31, 334.999)))
    rc, rec = run_running(tmp_path, fake)
    assert rc == 3 and rec["verdict"] == "halt_due" and rec["halt_due"] is True and rec["running_lt_latch"] is True
    assert rec["reasons"] == ["executor_running_median_lt_335"]  # distinct from the day reasons (DEC-028 s3.5)
    assert rec["executor"]["median_s"] == 334.999 and rec["executor"]["pools"] == 31 and rec["stop"]["present"] is True
    assert stop_calls(fake) == [("sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"), ("sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP")]
    assert unit_calls(fake) == 0
    assert x.decided(str(tmp_path / "exec-day.jsonl"), x.TOOL, "2026-10-12") is None  # a running line never settles a day


@pytest.mark.parametrize("fig,verdict", [
    (pools(31, 335.0), "no_halt"),       # at 335.0: no halt
    (pools(29, 300.0), "unevaluated"),   # fewer than 30 pools: never judged, exit 0 (an early-day count is not a failure)
    (pools(5, None), "unevaluated"),
    (None, "unevaluated"),               # no entry yet today
])
def test_running_mode_without_a_due_halt_is_exit_0_and_touches_nothing(tmp_path: Path, fig, verdict) -> None:
    fake = FakeRun(days_counters(d12=fig))
    rc, rec = run_running(tmp_path, fake)
    assert rc == 0 and rec["verdict"] == verdict and rec["halt_due"] is False and rec["stop"] is None and stop_calls(fake) == []


def test_running_halt_without_place_stop_is_exit_3_and_unverified_is_exit_6(tmp_path: Path) -> None:
    fake = FakeRun(days_counters(d12=pools(31, 334.0)))
    rc, rec = run_running(tmp_path, fake, place=False)
    assert rc == 3 and rec["stop"] is None and stop_calls(fake) == []
    rc, rec = run_running(tmp_path, FakeRun(days_counters(d12=pools(31, 334.0)), test_rc=1))
    assert rc == 6 and rec["stop"]["present"] is False


@pytest.mark.parametrize("argv,err", [
    (["--mode", "running", "--day", "2026-10-11"], "running_day_not_today"),
    (["--mode", "running", "--day", "2026-10-11", "--place-stop"], "running_day_not_today"),
])
def test_running_mode_refusals(tmp_path: Path, argv, err) -> None:
    fake = FakeRun(days_counters(d12=pools(31, 334.0)))
    rc = x.main(argv + ["--log", str(tmp_path / "l.jsonl")], now=datetime(2026, 10, 12, 5, 47, tzinfo=timezone.utc), run=fake)
    assert rc == 2 and json.loads((tmp_path / "l.jsonl").read_text())["error"] == err and fake.calls == []


# ---- --dry-read: the manager's read-path proof before the first enforcing read (10-12T00:21Z) ----

@pytest.mark.parametrize("day,now", [("2026-10-10", datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)),   # first-strike day: refused by day mode
                                     ("2026-10-11", datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc))])  # today, incomplete
def test_dry_read_reads_any_day_writes_nothing_and_never_places_stop(tmp_path: Path, capsys, day, now) -> None:
    fake = FakeRun(days_counters(d10=pools(40, 339.573), d11=pools(5, None)))
    log = tmp_path / "exec-day.jsonl"
    rc = x.main(["--mode", "day", "--day", day, "--dry-read", "--log", str(log)], now=now, run=fake)
    rec = json.loads(capsys.readouterr().out)
    assert rc == 0 and rec["dry_read"] is True and rec["verdict"] == "dry_read" and rec["halt_due"] is False and rec["stop"] is None
    assert rec["would_verdict"] in ("no_halt", "unevaluated") and rec["would_halt_due"] is False
    assert fake.calls[0] == x.DD_ARGV and stop_calls(fake) == []
    assert not log.exists(), "a dry read never writes the log"


def test_dry_read_shows_a_would_be_halt_without_touching_stop(tmp_path: Path, capsys) -> None:
    fake = FakeRun(days_counters(d11=None, d12=None))
    rc = x.main(["--mode", "day", "--day", "2026-10-12", "--dry-read"], now=NOW_13, run=fake)
    rec = json.loads(capsys.readouterr().out)
    assert rc == 0 and rec["would_verdict"] == "halt_due" and rec["would_halt_due"] is True and rec["reasons"] == ["two_consecutive_unevaluated_days"]
    assert stop_calls(fake) == []


def test_dry_read_refuses_place_stop_and_reports_read_failures(tmp_path: Path, capsys) -> None:
    fake = FakeRun(days_counters(d11=None))
    assert x.main(["--mode", "day", "--day", "2026-10-11", "--dry-read", "--place-stop"], now=NOW_13, run=fake) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "dry_read_never_places_stop" and fake.calls == []
    assert x.main(["--mode", "day", "--day", "2026-10-11", "--dry-read"], now=NOW_13, run=FakeRun(dd_rc=1, raw=b"")) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "sudo_dd_failed"


def test_a_dry_read_line_is_never_final(tmp_path: Path) -> None:
    log = tmp_path / "exec-day.jsonl"
    log.write_text(json.dumps({"tool": x.TOOL, "mode": "day", "day": "2026-10-11", "exit": 0, "dry_read": True}) + "\n")
    assert x.decided(str(log), x.TOOL, "2026-10-11") is None


def test_log_is_required_outside_dry_read(tmp_path: Path, capsys) -> None:
    assert x.main(["--mode", "day", "--day", "2026-10-11"], now=NOW, run=FakeRun(counters())) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "log_required"


def test_halt_places_stop_through_the_allowlisted_read(tmp_path: Path) -> None:
    fake = FakeRun(counters(median=336.999, pools=40))
    rc, rec, text = run_day(tmp_path, fake)
    assert rc == 3 and rec["verdict"] == "halt_due" and rec["halt_due"] is True
    assert fake.calls[0] == ("sudo", "-n", "/usr/bin/dd", "iflag=nofollow", "status=none", "if=/var/lib/mal-live/h5/live/h5-counters.json")
    assert stop_calls(fake) == [("sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"), ("sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP")]
    assert rec["stop"]["present"] is True and rec["executor"] == {"entry": True, "median_s": 336.999, "pools": 40, "pools_source": "boost_s"}
    assert "executor_median_lt_337" in rec["reasons"]
    assert str(SENTINEL) not in text and "SENTINELHALT" not in text and "realized" not in text and "trades" not in text


def test_at_337_is_no_halt_and_touches_nothing(tmp_path: Path) -> None:
    fake = FakeRun(counters(median=337.0, pools=30))
    rc, rec, _ = run_day(tmp_path, fake)
    assert rc == 0 and rec["verdict"] == "no_halt" and rec["stop"] is None and stop_calls(fake) == []


@pytest.mark.parametrize("median,pools,day,reason", [
    (336.0, 29, "2026-10-11", "unevaluated_lt_30_pools"),
    (None, 10, "2026-10-11", "unevaluated_lt_30_pools"),
    (336.0, 40, "2026-10-09", "no_executor_day_entry"),  # the entry is under another day; 10-11 has none
])
def test_unevaluated_is_exit_5_without_stop(tmp_path: Path, median, pools, day, reason) -> None:
    fake = FakeRun(counters(day=day, median=median, pools=pools))
    rc, rec, _ = run_day(tmp_path, fake)
    assert rc == 5 and rec["verdict"] == "unevaluated" and rec["reasons"] == [reason] and stop_calls(fake) == []


def test_trimmed_boost_list_uses_the_executor_invariant(tmp_path: Path) -> None:
    fake = FakeRun(counters(median=335.5, pools=None))  # the executor drops boost_s for days older than its last 3 keys
    rc, rec, _ = run_day(tmp_path, fake)
    assert rc == 3 and rec["executor"]["pools"] is None and rec["executor"]["pools_source"] == "executor_invariant"


def test_stop_not_verified_is_exit_6(tmp_path: Path) -> None:
    rc, rec, _ = run_day(tmp_path, FakeRun(counters(median=330.0), test_rc=1))
    assert rc == 6 and rec["stop"]["present"] is False


def test_without_place_stop_a_halt_is_exit_3_and_stop_untouched(tmp_path: Path) -> None:
    fake = FakeRun(counters(median=330.0))
    rc, rec, _ = run_day(tmp_path, fake, place=False)
    assert rc == 3 and rec["stop"] is None and stop_calls(fake) == []


@pytest.mark.parametrize("fake,err", [
    (FakeRun(dd_rc=1, raw=b""), "sudo_dd_failed"),
    (FakeRun(raw=b"not json"), "counters_not_json"),
    (FakeRun(raw=b""), "counters_empty"),
    (FakeRun(raw=json.dumps({"schema": "other", "days": {}}).encode()), "counters_schema"),
    (FakeRun(raw=b" " * (x.MAX_READ + 1)), "counters_too_big"),
])
def test_read_failures_are_input_errors_never_absent(tmp_path: Path, fake: FakeRun, err: str) -> None:
    rc, rec, _ = run_day(tmp_path, fake)
    assert rc == 2 and rec["error"] == err and rec["verdict"] == "input_error" and stop_calls(fake) == []


@pytest.mark.parametrize("day,now,err", [
    ("2026-10-10", NOW, "day_not_after_first_strike_day"),
    ("2026-10-12", NOW, "day_not_complete"),
    ("2026-13-01", NOW, "bad_date"),
])
def test_bad_days_are_refused_before_any_read(tmp_path: Path, day: str, now: datetime, err: str) -> None:
    fake = FakeRun(counters())
    rc, rec, _ = run_day(tmp_path, fake, day=day, now=now)
    assert rc == 2 and rec["error"] == err and fake.calls == []


def _line(tool: str, day: str, ex: int, present: bool | None = None, **kw) -> str:
    rec = {"tool": tool, "mode": "day", "day": day, "exit": ex, "verdict": "x", "stop": None if present is None else {"present": present}}
    rec.update(kw)
    return json.dumps(rec)


@pytest.mark.parametrize("lines,want", [
    ([_line("h5_boost_day_check", "2026-10-11", 0)], True),
    ([_line("h5_boost_day_check", "2026-10-11", 5)], True),
    ([_line("h5_boost_day_check", "2026-10-11", 3, True)], True),
    ([_line("h5_boost_day_check", "2026-10-11", 3, None)], False),   # a halt logged without --place-stop: not settled
    ([_line("h5_boost_day_check", "2026-10-11", 6, False)], False),
    ([_line("h5_boost_day_check", "2026-10-11", 2)], False),
    ([_line("h5_boost_day_check", "2026-10-11", 0, now_overridden=True)], False),
    ([_line("h5_boost_day_check", "2026-10-12", 0)], False),
    ([_line("h5_exec_day_check", "2026-10-11", 0)], False),          # another tool's line
    ([json.dumps({"tool": "h5_boost_day_check", "mode": "running", "day": "2026-10-11", "exit": 0})], False),
    (["garbage", _line("h5_boost_day_check", "2026-10-11", 6, False), _line("h5_boost_day_check", "2026-10-11", 3, True)], True),
])
def test_decided(tmp_path: Path, capsys, lines: list[str], want: bool) -> None:
    log = tmp_path / "boost-day.jsonl"
    log.write_text("\n".join(lines) + "\n")
    rc = x.main(["--mode", "decided", "--day", "2026-10-11", "--tool", "h5_boost_day_check", "--log", str(log)])
    out = json.loads(capsys.readouterr().out)
    assert (rc == 0) is want and out["decided"] is want
    assert log.read_text() == "\n".join(lines) + "\n", "decided mode never writes"


def test_decided_missing_log_is_not_decided(tmp_path: Path) -> None:
    assert x.main(["--mode", "decided", "--day", "2026-10-11", "--tool", "h5_exec_day_check", "--log", str(tmp_path / "none")]) == 1


def test_own_lines_are_read_back_as_decided(tmp_path: Path) -> None:
    run_day(tmp_path, FakeRun(counters(median=330.0)))
    assert x.decided(str(tmp_path / "exec-day.jsonl"), x.TOOL, "2026-10-11") is None  # tests stamp now_overridden: never a real verdict
    rec = json.loads((tmp_path / "exec-day.jsonl").read_text())
    rec["now_overridden"] = False
    (tmp_path / "exec-day.jsonl").write_text(json.dumps(rec) + "\n")
    assert x.decided(str(tmp_path / "exec-day.jsonl"), x.TOOL, "2026-10-11")["exit"] == 3


def test_never_removes_stop_and_reads_only_by_dd() -> None:
    src = (ROOT / "tools" / "h5_exec_day_check.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for bad in ("os.remove", "os.unlink", "unlink(", "rmtree", '"rm"', "'rm'", "cat "):
        assert bad not in code, bad
    assert re.search(r'DD_ARGV = \("sudo", "-n", "/usr/bin/dd", "iflag=nofollow", "status=none", f"if=\{COUNTERS_PATH\}"\)', code)


def test_the_read_is_in_the_daily_checks_allowlist() -> None:
    spec = importlib.util.spec_from_file_location("h5_daily_check_mod", ROOT / "scripts" / "mal-fast" / "h5-daily-check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h5_daily_check_mod"] = mod
    spec.loader.exec_module(mod)
    assert x.COUNTERS_PATH in mod.PRIV_READ
    assert x.DD_ARGV[2:] == mod.dd_argv(x.COUNTERS_PATH)


def test_cli_help_runs_isolated() -> None:
    cp = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "h5_exec_day_check.py"), "--help"], capture_output=True, text=True,
                        stdin=subprocess.DEVNULL, timeout=30)
    assert cp.returncode == 0 and "Never removes STOP" in cp.stdout
