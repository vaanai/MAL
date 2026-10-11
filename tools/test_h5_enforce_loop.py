"""scripts/mal-fast/h5-enforce-loop.sh: shell syntax, the dry-run schedule, and the run loop against a stub tool.

The stub stands in for tools/h5_boost_day_check.py: it records its argv and exits with STUB_RC. It never calls sudo and never touches
/var/lib/mal-live. Every path is under tmp_path. The run tests use the real clock (the loop refuses a test clock outside --dry-run), so
they set the state file and END around "now" so that the loop catches up past slots and ends without sleeping.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mal-fast" / "h5-enforce-loop.sh"
SH = shutil.which("dash") or "/bin/sh"  # MiScusi runs job commands with sh (dash on mal-fast-0)
DAY_OFF = 1260
OFFS = (1260, 20820, 42420, 64020, 85620)  # 00:21 05:47 11:47 17:47 23:47 UTC

STUB = r'''
import json, os, sys
args = sys.argv[1:]
if args == ["--help"]:
    sys.exit(0)
with open(os.environ["STUB_CALLS"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
mode = args[args.index("--mode") + 1]
day = args[args.index("--day") + 1] if "--day" in args else "today"
rc = int(os.environ.get("STUB_RC", "0"))
verdict = {0: "no_halt", 2: "input_error", 3: "halt_due", 5: "unevaluated", 6: "halt_due"}.get(rc, "crash")
rec = {"tool": "h5_boost_day_check", "v": 2, "mode": mode, "day": day, "verdict": verdict, "halt_due": rc in (3, 6),
       "stop": ({"present": rc == 3} if rc in (3, 6) else None), "exit": rc}
print(json.dumps(rec, separators=(",", ":")))
sys.exit(rc)
'''


def iso(e: int) -> str:
    return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(e: int) -> str:
    return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%d")


def next_slot(t: int) -> int:
    d = t - t % 86400
    for o in OFFS:
        if d + o > t:
            return d + o
    return d + 86400 + DAY_OFF


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("H5_ENFORCE_", "MISCUSI_", "MAL_BDC_"))}
    env.update({
        "H5_ENFORCE_SHADOW_DIR": str(tmp_path / "shadow"),
        "H5_ENFORCE_CHECK_LOG": str(tmp_path / "daily" / "boost-day.jsonl"),
        "H5_ENFORCE_HEARTBEAT": str(tmp_path / "daily" / "enforce-loop.jsonl"),
        "H5_ENFORCE_STATE": str(tmp_path / "ckpt" / "h5-enforce-loop.state"),
    })
    env.update(extra)
    return env


def run(args: list[str], env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([SH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def test_shell_syntax() -> None:
    for sh in {SH, "/bin/sh"}:
        subprocess.run([sh, "-n", str(SCRIPT)], check=True, stdin=subprocess.DEVNULL)


def test_help_prints_the_header_only(tmp_path: Path) -> None:
    cp = run(["--help"], env_for(tmp_path))
    assert cp.returncode == 0
    lines = cp.stdout.splitlines()
    assert lines[0].startswith("# H5 halt enforcement loop") and all(l.startswith("#") for l in lines)
    assert any("never removes" in l for l in lines)


def test_never_removes_or_names_stop() -> None:
    text = SCRIPT.read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"\brm\b", code), "the loop must not remove anything"
    assert "/var/lib/mal-live" not in code and "STOP" not in code.replace("STOP_RETRIES", ""), "only the tool's --place-stop touches STOP"
    assert "--place-stop" in code
    for bashism in ("PIPESTATUS", "[[", "local ", "function ", "<<<", "$'"):
        assert bashism not in code, bashism


def test_dry_run_schedule(tmp_path: Path) -> None:
    env = env_for(tmp_path, H5_ENFORCE_NOW="2026-10-10T23:00:00Z")
    env.pop("H5_ENFORCE_SHADOW_DIR"); env.pop("H5_ENFORCE_CHECK_LOG")  # the defaults: $HOME/data/...
    home = env["HOME"]
    cp = run(["--dry-run"], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    slots = [l for l in lines if l.startswith("SLOT ")]
    assert slots[0] == f"SLOT 2026-10-10T23:47:00Z running day=2026-10-10 run: /usr/bin/python3 -I tools/h5_boost_day_check.py --shadow-dir {home}/data/h5-shadow --mode running --place-stop --log {home}/data/h5-daily/boost-day.jsonl"
    assert slots[1] == "SLOT 2026-10-11T00:21:00Z day day=2026-10-10 skip:before_first_day"
    assert slots[2].startswith("SLOT 2026-10-11T05:47:00Z running day=2026-10-11 run: ")
    assert f"SLOT 2026-10-12T00:21:00Z day day=2026-10-11 run: /usr/bin/python3 -I tools/h5_boost_day_check.py --shadow-dir {home}/data/h5-shadow --mode day --day 2026-10-11 --place-stop --log {home}/data/h5-daily/boost-day.jsonl" in slots
    times = [l.split()[1][11:16] for l in slots]
    assert set(times) == {"00:21", "05:47", "11:47", "17:47", "23:47"}
    assert slots[-1].startswith("SLOT 2026-10-23T23:47:00Z running ")
    day_runs = [l for l in slots if " day day=" in l and " run: " in l]
    assert [l.split()[3] for l in day_runs] == [f"day=2026-10-{d:02d}" for d in range(11, 23)]  # 10-11 .. 10-22; 10-23's is after END
    assert lines[-1] == "END 2026-10-24T00:00:00Z slots=66 run=65 skip=1"
    assert not (tmp_path / "daily").exists() and not (tmp_path / "ckpt").exists(), "a dry run writes nothing"


def test_dry_run_commands_parse_with_the_real_tool(tmp_path: Path) -> None:
    """Every scheduled command is accepted by tools/h5_boost_day_check.py's own parser (parsed only, never run: no STOP is touched)."""
    from tools.h5_boost_day_check import build_parser

    cp = run(["--dry-run"], env_for(tmp_path, H5_ENFORCE_NOW="2026-10-11T04:00:00Z"))
    cmds = [l.split(" run: ", 1)[1].split() for l in cp.stdout.splitlines() if " run: " in l]
    assert cmds
    for words in cmds:
        assert words[:3] == ["/usr/bin/python3", "-I", "tools/h5_boost_day_check.py"]
        a = build_parser().parse_args(words[3:])
        assert a.place_stop is True and a.mode in ("day", "running") and a.shadow_dir == str(tmp_path / "shadow")
        assert (a.day is None) == (a.mode == "running")


@pytest.mark.parametrize("extra,err", [
    ({"H5_ENFORCE_NOW": "2026-10-11T00:00:00Z"}, "h5_enforce_now_is_dry_run_only"),
    ({"MAL_BDC_NOW": "2026-10-11T00:00:00Z"}, "mal_bdc_now_set"),
    ({"H5_ENFORCE_END": "not-a-date"}, "bad_end"),
    ({"H5_ENFORCE_FIRST_DAY": "10/11/2026"}, "bad_first_day"),
    ({"H5_ENFORCE_TOOL": "/nonexistent/h5_boost_day_check.py"}, "tool_missing"),
])
def test_config_errors_exit_2(tmp_path: Path, extra: dict[str, str], err: str) -> None:
    cp = run([], env_for(tmp_path, **extra))
    assert cp.returncode == 2
    assert json.loads(cp.stdout.strip().splitlines()[-1])["error"] == err


def _catch_up_setup(tmp_path: Path, stub_rc: int, **extra: str) -> tuple[dict[str, str], int, int]:
    """State = 2 days back; END = the first slot after now (so the loop catches up and ends without sleeping)."""
    now = int(time.time())
    if next_slot(now) - now < 20:  # do not straddle a slot boundary between the test's clock and the loop's
        time.sleep(next_slot(now) - now + 1)
        now = int(time.time())
    end = next_slot(now)
    last_done = now - now % 86400 - 2 * 86400 - 1
    (tmp_path / "ckpt").mkdir()
    (tmp_path / "ckpt" / "h5-enforce-loop.state").write_text(f"{last_done} 0 0 0 0 0\n")
    stub = tmp_path / "stub_tool.py"
    stub.write_text(STUB)
    env = env_for(tmp_path, H5_ENFORCE_TOOL=str(stub), H5_ENFORCE_FIRST_DAY="2000-01-01", H5_ENFORCE_END=iso(end),
                  H5_ENFORCE_RETRY_S="0", STUB_RC=str(stub_rc), STUB_CALLS=str(tmp_path / "calls.jsonl"),
                  MISCUSI_RESULT=str(tmp_path / "result.json"), MISCUSI_PROGRESS=str(tmp_path / "progress.json"), **extra)
    return env, now, last_done


def _expected(now: int, last_done: int) -> list[list[str]]:
    """Every past day slot runs (late) for the previous day; a past running slot runs only if it is today's and the last past one."""
    out: list[list[str]] = []
    t = last_done
    while True:
        s = next_slot(t)
        if s > now:
            return out
        if s % 86400 == DAY_OFF:
            out.append(["day", day_of(s - DAY_OFF - 86400)])
        elif day_of(s) == day_of(now) and not (next_slot(s) <= now and next_slot(s) % 86400 != DAY_OFF):
            out.append(["running", "today"])
        t = s


def _calls(tmp_path: Path) -> list[list[str]]:
    p = tmp_path / "calls.jsonl"
    rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return [[r[r.index("--mode") + 1], r[r.index("--day") + 1] if "--day" in r else "today"] for r in rows]


def _no_dup(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), f"duplicate heartbeat key: {keys}"
    return dict(pairs)


def _heartbeat(tmp_path: Path) -> list[dict]:
    return [json.loads(l, object_pairs_hook=_no_dup) for l in (tmp_path / "daily" / "enforce-loop.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("stub_rc", [0, 2, 3, 5, 1])
def test_catch_up_runs_and_tool_verdicts_never_fail_the_loop(tmp_path: Path, stub_rc: int) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, stub_rc)
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    exp = _expected(now, last_done)
    assert _calls(tmp_path) == exp
    assert sum(1 for m, _ in exp if m == "day") == (3 if now % 86400 > DAY_OFF else 2)  # 00:21 of the two back days, and today's once past
    for args in (json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()):
        assert "--place-stop" in args
        assert args[args.index("--shadow-dir") + 1] == str(tmp_path / "shadow")
        assert args[args.index("--log") + 1] == str(tmp_path / "daily" / "boost-day.jsonl")
    hb = _heartbeat(tmp_path)
    assert hb[0]["event"] == "start" and hb[0]["resumed"] is True and hb[-1]["event"] == "end"
    runs = [r for r in hb if r["event"] == "run"]
    assert len(runs) == len(exp) and all(r["rc"] == stub_rc for r in runs)
    assert all(r["halt_due"] is (stub_rc == 3) for r in runs)
    skips = [r for r in hb if r["event"] == "skip"]
    assert {r["reason"] for r in skips} <= {"stale_running_day", "superseded_by_later_running_slot"}
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["ok"] is True and res["metrics"]["runs"] == len(exp)
    assert res["metrics"]["tool_errors"] == (len(exp) if stub_rc == 1 else 0)
    assert res["metrics"]["halt_due_runs"] == (len(exp) if stub_rc == 3 else 0)
    state = (tmp_path / "ckpt" / "h5-enforce-loop.state").read_text().split()
    assert int(state[0]) < next_slot(now) and int(state[0]) > now - 86400  # the last past slot
    json.loads((tmp_path / "progress.json").read_text())


def test_stop_not_verified_is_retried(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 6, H5_ENFORCE_STOP_RETRIES="2")
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    exp = _expected(now, last_done)
    assert _calls(tmp_path) == [c for c in exp for _ in range(3)]  # first try + 2 retries per slot
    hb = _heartbeat(tmp_path)
    assert sum(1 for r in hb if r["event"] == "retry") == 2 * len(exp)
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["metrics"]["stop_unverified"] == 3 * len(exp)


def test_fresh_start_does_not_catch_up_and_resume_does(tmp_path: Path) -> None:
    env, now, _ = _catch_up_setup(tmp_path, 0)
    (tmp_path / "ckpt" / "h5-enforce-loop.state").unlink()
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _calls(tmp_path) == []  # earlier slots are the session crons' job
    hb = _heartbeat(tmp_path)
    assert hb[0]["resumed"] is False and hb[-1]["event"] == "end"


def test_sigterm_logs_and_exits_143(tmp_path: Path) -> None:
    stub = tmp_path / "stub_tool.py"
    stub.write_text(STUB)
    env = env_for(tmp_path, H5_ENFORCE_TOOL=str(stub), H5_ENFORCE_END=iso(int(time.time()) + 3 * 86400),
                  STUB_CALLS=str(tmp_path / "calls.jsonl"))
    p = subprocess.Popen([SH, str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    hbf = tmp_path / "daily" / "enforce-loop.jsonl"
    deadline = time.time() + 20
    while time.time() < deadline and not (hbf.exists() and '"event":"wait"' in hbf.read_text()):
        time.sleep(0.1)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=20)
    assert p.returncode == 143, out
    hb = _heartbeat(tmp_path)
    assert [r["event"] for r in hb][-2:] == ["wait", "signal"]
    assert hb[-1]["signal"] == "TERM"
    assert _calls(tmp_path) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
