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


class FakeRun:
    def __init__(self, counters: object = None, dd_rc: int = 0, raw: bytes | None = None, test_rc: int = 0) -> None:
        self.raw = raw if raw is not None else json.dumps(counters).encode()
        self.dd_rc, self.test_rc = dd_rc, test_rc
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kw):
        self.calls.append(tuple(argv))
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
    return [c for c in fake.calls if "/usr/bin/dd" not in c]


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
