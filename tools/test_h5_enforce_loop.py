"""scripts/mal-fast/h5-enforce-loop.sh: shell syntax, the dry-run schedule, and the run loop against stub tools.

The stubs stand in for tools/h5_exec_day_check.py (the executor's figure) and tools/h5_boost_day_check.py (the shadow's): each records
its argv, appends a day line to its --log like the real tool and exits with STUB_RC (or STUB_RC_EXEC / STUB_RC_SHADOW). The exec stub
hands `--mode decided` to the REAL exec tool (it only reads a log). No stub calls sudo or touches /var/lib/mal-live. Every path is under
tmp_path. The run tests use the real clock (the loop refuses a test clock outside --dry-run), so they set the state file and END around
"now" so that the loop catches up past slots and ends without sleeping.
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
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mal-fast" / "h5-enforce-loop.sh"
REAL_EXEC = ROOT / "tools" / "h5_exec_day_check.py"
SH = shutil.which("dash") or "/bin/sh"  # MiScusi runs job commands with sh (dash on mal-fast-0)
DAY_OFF = 1260
OFFS = (1260, 20820, 42420, 64020, 85620)  # 00:21 05:47 11:47 17:47 23:47 UTC
RETRIES = 2

STUB = r'''
import json, os, sys, time
args = sys.argv[1:]
if args == ["--help"]:
    sys.exit(0)
kind = "exec" if "exec" in os.path.basename(sys.argv[0]) else "shadow"
mode = args[args.index("--mode") + 1]
if mode == "decided":
    os.execv(sys.executable, [sys.executable, "-I", os.environ["REAL_EXEC"], *args])
with open(os.environ["STUB_CALLS"], "a") as fh:
    fh.write(json.dumps([kind] + args) + "\n")
if os.environ.get("STUB_PID"):
    with open(os.environ["STUB_PID"], "w") as fh:
        fh.write(str(os.getpid()))
time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
day = args[args.index("--day") + 1] if "--day" in args else "none"
rc = int(os.environ.get("STUB_RC_" + kind.upper(), os.environ.get("STUB_RC", "0")))
seq = os.environ.get("STUB_RC_SEQ")
if seq and os.path.exists(seq):  # a queue of rcs, one per check run, then STUB_RC
    with open(seq) as fh:
        q = fh.read().split()
    if q:
        rc = int(q[0])
        with open(seq, "w") as fh:
            fh.write(" ".join(q[1:]))
verdict = {0: "no_halt", 2: "input_error", 3: "halt_due", 5: "unevaluated", 6: "halt_due"}.get(rc, "crash")
tool = "h5_exec_day_check" if kind == "exec" else "h5_boost_day_check"
rec = {"tool": tool, "v": 2, "mode": mode, "day": day, "now_overridden": False, "verdict": verdict, "halt_due": rc in (3, 6),
       "stop": ({"present": rc == 3} if rc in (3, 6) else None), "exit": rc}
line = json.dumps(rec, separators=(",", ":"))
with open(args[args.index("--log") + 1], "a") as fh:
    fh.write(line + "\n")
print(line)
if os.environ.get("STUB_QUIT_ON") == kind + ":" + mode:
    open(os.environ["STUB_QUIT_FILE"], "w").close()
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
        "H5_ENFORCE_EXEC_LOG": str(tmp_path / "daily" / "exec-day.jsonl"),
        "H5_ENFORCE_HEARTBEAT": str(tmp_path / "daily" / "enforce-loop.jsonl"),
        "H5_ENFORCE_BEAT": str(tmp_path / "daily" / "enforce-loop.beat"),
        "H5_ENFORCE_QUIT_FILE": str(tmp_path / "daily" / "enforce-loop.quit"),
        "H5_ENFORCE_STATE": str(tmp_path / "ckpt" / "h5-enforce-loop.state"),
    })
    env.update(extra)
    return env


def run(args: list[str], env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([SH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def code_lines() -> str:
    return "\n".join(l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_shell_syntax() -> None:
    for sh in {SH, "/bin/sh"}:
        subprocess.run([sh, "-n", str(SCRIPT)], check=True, stdin=subprocess.DEVNULL)


def test_help_prints_the_header_only(tmp_path: Path) -> None:
    cp = run(["--help"], env_for(tmp_path))
    assert cp.returncode == 0
    lines = cp.stdout.splitlines()
    assert lines[0].startswith("# H5 halt enforcement loop") and all(l.startswith("#") for l in lines)
    assert any("never removes" in l for l in lines) and any("enforce-loop.quit" in l for l in lines)


def test_never_removes_or_names_stop_and_runs_tools_promptly() -> None:
    code = code_lines()
    assert not re.search(r"\brm\b|\bunlink\b", code), "the loop must not remove anything"
    assert "/var/lib/mal-live" not in code and "STOP" not in code, "only the tools' --place-stop touch STOP"
    assert code.count("--place-stop") == 4  # exec day/running and shadow day/running, run and dry-run text of each
    # every tool run is in the background under `timeout --foreground` (signals handled at once; a group kill reaches the tool)
    assert re.search(r'timeout --foreground -k 30 "\$TOOL_TIMEOUT_S" "\$@" > "\$OUT_FILE" 2>&1 &\n\s+TOOL_PID=\$!', code)
    assert "$(timeout" not in code
    for bashism in ("PIPESTATUS", "[[", "local ", "function ", "<<<", "$'"):
        assert bashism not in code, bashism


def test_dry_run_schedule(tmp_path: Path) -> None:
    env = env_for(tmp_path, H5_ENFORCE_NOW="2026-10-10T23:00:00Z")
    for k in ("H5_ENFORCE_SHADOW_DIR", "H5_ENFORCE_CHECK_LOG", "H5_ENFORCE_EXEC_LOG"):
        env.pop(k)  # the defaults: $HOME/data/...
    home = env["HOME"]
    cp = run(["--dry-run"], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    slots = [l for l in lines if l.startswith("SLOT ")]
    assert not any(l.startswith("CATCHUP ") for l in lines)  # nothing to catch up before 10-12T00:21Z
    assert slots[0] == (f"SLOT 2026-10-10T23:47:00Z running day=2026-10-10 check=shadow run: /usr/bin/python3 -I tools/h5_boost_day_check.py "
                        f"--shadow-dir {home}/data/h5-shadow --mode running --day 2026-10-10 --place-stop --log {home}/data/h5-daily/boost-day.jsonl")
    assert slots[1] == (f"SLOT 2026-10-10T23:47:00Z running day=2026-10-10 check=exec run: /usr/bin/python3 -I tools/h5_exec_day_check.py "
                        f"--mode running --day 2026-10-10 --place-stop --log {home}/data/h5-daily/exec-day.jsonl")  # Am.5 item 6: < 335 s
    assert slots[2] == "SLOT 2026-10-11T00:21:00Z day day=2026-10-10 skip:before_first_day"
    assert slots[3].startswith("SLOT 2026-10-11T05:47:00Z running day=2026-10-11 check=shadow run: ")
    # the executor's figure at EVERY slot: the running report, and from 10-12 a second chance for yesterday's day check
    running_slots = sorted({l.split()[1] for l in slots if " running " in l})
    assert all(sum(1 for l in slots if l.split()[1] == t and " check=exec run: " in l and "--mode running" in l) == 1 for t in running_slots)
    rechecks = [l for l in slots if " check=exec-recheck run: " in l]
    assert len(rechecks) == 12 * 4 and rechecks[0].startswith("SLOT 2026-10-12T05:47:00Z running day=2026-10-11 check=exec-recheck run: ")
    assert all("--mode day" in l and "--place-stop" in l for l in rechecks)
    i = slots.index(f"SLOT 2026-10-12T00:21:00Z day day=2026-10-11 check=exec run: /usr/bin/python3 -I tools/h5_exec_day_check.py "
                    f"--mode day --day 2026-10-11 --place-stop --log {home}/data/h5-daily/exec-day.jsonl")
    assert slots[i + 1] == (f"SLOT 2026-10-12T00:21:00Z day day=2026-10-11 check=shadow run: /usr/bin/python3 -I tools/h5_boost_day_check.py "
                            f"--shadow-dir {home}/data/h5-shadow --mode day --day 2026-10-11 --place-stop --log {home}/data/h5-daily/boost-day.jsonl")
    assert {l.split()[1][11:16] for l in slots} == {"00:21", "05:47", "11:47", "17:47", "23:47"}
    assert slots[-1].startswith("SLOT 2026-10-23T23:47:00Z running ")
    for check in ("exec", "shadow"):
        days = [l.split()[3] for l in slots if " day day=" in l and f" check={check} run: " in l]
        assert days == [f"day=2026-10-{d:02d}" for d in range(11, 23)]  # 10-11 .. 10-22; 10-23's is after END
    # 12 day slots x 2 + 5 running slots before 10-12 x 2 + 48 running slots x 3 (recheck, shadow, exec report)
    assert lines[-1] == "END 2026-10-24T00:00:00Z slots=66 commands=178 skip=1 fresh_start_catch_up=0"
    assert not (tmp_path / "daily").exists() and not (tmp_path / "ckpt").exists(), "a dry run writes nothing"


def test_dry_run_commands_parse_with_the_real_tools(tmp_path: Path) -> None:
    """Every scheduled command is accepted by the real tool's own parser (parsed only, never run: no STOP is touched)."""
    from tools.h5_boost_day_check import build_parser as shadow_parser
    from tools.h5_exec_day_check import build_parser as exec_parser

    cp = run(["--dry-run"], env_for(tmp_path, H5_ENFORCE_NOW="2026-10-13T04:00:00Z"))
    rows = [l for l in cp.stdout.splitlines() if " run: " in l]
    catch = [l for l in rows if l.startswith("CATCHUP ")]
    assert [l.split()[3] for l in catch] == ["day=2026-10-11", "day=2026-10-12"]  # a fresh start judges the executor's figure for both
    assert cp.stdout.splitlines()[-1].endswith(" fresh_start_catch_up=2")
    for l in rows:
        slot_day = l.split()[3][4:]
        words = l.split(" run: ", 1)[1].split()
        assert words[:2] == ["/usr/bin/python3", "-I"]
        if " check=exec" in l:
            assert words[2] == "tools/h5_exec_day_check.py"
            a = exec_parser().parse_args(words[3:])
            assert a.day == slot_day and a.log == str(tmp_path / "daily" / "exec-day.jsonl") and a.dry_read is False
            if " running " in l and " check=exec run: " in l:
                assert a.mode == "running" and a.place_stop is True and l.split()[1][:10] == slot_day
            else:
                assert a.mode == "day" and a.place_stop is True
                if " check=exec-recheck " in l:
                    assert day_of(int(datetime.fromisoformat(l.split()[1].replace("Z", "+00:00")).timestamp()) - 86400) == slot_day
        else:
            assert words[2] == "tools/h5_boost_day_check.py"
            a = shadow_parser().parse_args(words[3:])
            assert a.place_stop is True and a.mode in ("day", "running") and a.shadow_dir == str(tmp_path / "shadow")
            assert a.day == slot_day  # running is pinned to the slot's own UTC day
            if a.mode == "running":
                assert l.split()[1][:10] == slot_day


@pytest.mark.parametrize("extra,err", [
    ({"H5_ENFORCE_NOW": "2026-10-11T00:00:00Z"}, "h5_enforce_now_is_dry_run_only"),
    ({"MAL_BDC_NOW": "2026-10-11T00:00:00Z"}, "mal_bdc_now_set"),
    ({"H5_ENFORCE_END": "not-a-date"}, "bad_end"),
    ({"H5_ENFORCE_FIRST_DAY": "10/11/2026"}, "bad_first_day"),
    ({"H5_ENFORCE_RETRIES": "11"}, "bad_number"),
    ({"H5_ENFORCE_TOOL": "/nonexistent/h5_boost_day_check.py"}, "tool_missing"),
    ({"H5_ENFORCE_EXEC_TOOL": "/nonexistent/h5_exec_day_check.py"}, "exec_tool_missing"),
])
def test_config_errors_exit_2(tmp_path: Path, extra: dict[str, str], err: str) -> None:
    cp = run([], env_for(tmp_path, **extra))
    assert cp.returncode == 2
    assert json.loads(cp.stdout.strip().splitlines()[-1])["error"] == err


def _stubs(tmp_path: Path) -> tuple[Path, Path]:
    ex, sh = tmp_path / "stub_exec.py", tmp_path / "stub_shadow.py"
    ex.write_text(STUB)
    sh.write_text(STUB)
    return ex, sh


def _catch_up_setup(tmp_path: Path, stub_rc: int, **extra: str) -> tuple[dict[str, str], int, int]:
    """State = 2 days back; END = the first slot after now (so the loop catches up and ends without sleeping)."""
    now = int(time.time())
    if next_slot(now) - now < 20 or now % 86400 > 86400 - 20:  # do not straddle a slot boundary or midnight
        time.sleep(next_slot(now) - now + 1)
        now = int(time.time())
    end = next_slot(now)
    last_done = now - now % 86400 - 2 * 86400 - 1
    (tmp_path / "ckpt").mkdir()
    (tmp_path / "ckpt" / "h5-enforce-loop.state").write_text(f"{last_done} 0 0 0 0 0\n")
    (tmp_path / "daily").mkdir()
    ex, sh = _stubs(tmp_path)
    env = env_for(tmp_path, H5_ENFORCE_TOOL=str(sh), H5_ENFORCE_EXEC_TOOL=str(ex), H5_ENFORCE_FIRST_DAY="2000-01-01", H5_ENFORCE_END=iso(end),
                  H5_ENFORCE_RETRY_S="0", H5_ENFORCE_RETRIES=str(RETRIES), STUB_RC=str(stub_rc), STUB_CALLS=str(tmp_path / "calls.jsonl"),
                  REAL_EXEC=str(REAL_EXEC), MISCUSI_RESULT=str(tmp_path / "result.json"), MISCUSI_PROGRESS=str(tmp_path / "progress.json"))
    env.update(extra)
    return env, now, last_done


def _expected(now: int, last_done: int, exec_rc: int) -> list[tuple[str, str, str]]:
    """Every past day slot runs (late) the exec check, then the shadow check, for the previous day; a past running slot runs only if it is
    today's and the last past one, pinned to today: first yesterday's exec day check again unless its log already holds a final line
    (the stub's exec rc 0, 3 or 5), then the shadow running check, then the exec running report."""
    out: list[tuple[str, str, str]] = []
    t = last_done
    while True:
        s = next_slot(t)
        if s > now:
            return out
        if s % 86400 == DAY_OFF:
            d = day_of(s - DAY_OFF - 86400)
            out += [("exec", "day", d), ("shadow", "day", d)]
        elif day_of(s) == day_of(now) and not (next_slot(s) <= now and next_slot(s) % 86400 != DAY_OFF):
            if exec_rc not in (0, 3, 5):
                out.append(("exec", "day", day_of(s - 86400)))
            out += [("shadow", "running", day_of(now)), ("exec", "running", day_of(now))]
        t = s


def _calls(tmp_path: Path) -> list[tuple[str, str, str]]:
    p = tmp_path / "calls.jsonl"
    rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return [(r[0], r[r.index("--mode") + 1], r[r.index("--day") + 1]) for r in rows]


def _no_dup(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), f"duplicate heartbeat key: {keys}"
    return dict(pairs)


def _heartbeat(tmp_path: Path) -> list[dict]:
    return [json.loads(l, object_pairs_hook=_no_dup) for l in (tmp_path / "daily" / "enforce-loop.jsonl").read_text().splitlines()]


def _beat(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "daily" / "enforce-loop.beat").read_text(), object_pairs_hook=_no_dup)


@pytest.mark.parametrize("stub_rc", [0, 3, 2, 5, 6, 1])
def test_catch_up_retries_and_counts(tmp_path: Path, stub_rc: int) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, stub_rc)
    cp = run([], env)
    assert cp.returncode == (4 if stub_rc == 6 else 0), cp.stdout + cp.stderr  # END with the stop-unverified flag set: exit 4
    exp = _expected(now, last_done, stub_rc)
    tries = 1 if stub_rc in (0, 3) else 1 + RETRIES  # any rc but 0 and 3 is re-run
    assert _calls(tmp_path) == [c for c in exp for _ in range(tries)]
    assert sum(1 for k, m, _ in exp if (k, m) == ("shadow", "day")) == (3 if now % 86400 > DAY_OFF else 2)  # two back days, today's once past
    for row in (json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()):
        args = row[1:]
        assert "--place-stop" in args  # every check, also the exec running median (Am.5 item 6)
        assert args[args.index("--log") + 1] in (str(tmp_path / "daily" / "boost-day.jsonl"), str(tmp_path / "daily" / "exec-day.jsonl"))
    hb = _heartbeat(tmp_path)
    assert hb[0]["event"] == "start" and hb[0]["resumed"] is True and hb[-1]["event"] == "end"
    runs = [r for r in hb if r["event"] == "run"]
    assert len(runs) == len(exp) * tries and all(r["rc"] == stub_rc for r in runs)
    assert [(r["check"], r["mode"], r["day"]) for r in runs] == [c for c in exp for _ in range(tries)]
    assert sum(1 for r in hb if r["event"] == "retry") == len(exp) * (tries - 1)
    assert sum(1 for r in hb if r["event"] == "gave_up") == (0 if stub_rc in (0, 3) else len(exp))
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["ok"] is (stub_rc != 6)
    m = res["metrics"]
    assert m["runs"] == len(runs)
    assert m["tool_errors"] == (len(runs) if stub_rc in (1, 2) else 0)  # rc 2 is a tool error
    assert m["halt_due_runs"] == (len(runs) if stub_rc in (3, 6) else 0)
    assert m["stop_unverified"] == (len(runs) if stub_rc == 6 else 0)
    keys = [f"{k}:{md}:{d}" for k, md, d in exp]
    assert m["stop_unverified_open"] == (len(set(keys)) if stub_rc == 6 else 0)
    state = (tmp_path / "ckpt" / "h5-enforce-loop.state").read_text().split()
    assert now - 86400 < int(state[0]) < next_slot(now)
    if stub_rc == 6:
        assert int(state[8]) > 0 and state[9:] == list(dict.fromkeys(keys))  # every unverified check, kept in the state file
    else:
        assert state[8:] == ["0"]
    beat = _beat(tmp_path)
    assert beat["state"] == "exited" and beat["event"] == "end"
    assert beat["last_fail_rc"] == (0 if stub_rc in (0, 3) else stub_rc)
    assert (beat["stop_unverified_s"] > 0) is (stub_rc == 6) and beat["stop_unverified_n"] == m["stop_unverified_open"]
    chk = run(["--check-beat"], env)
    if stub_rc in (0, 3):
        assert chk.returncode == 0 and chk.stdout.startswith("EXITED event=end"), chk.stdout
    elif stub_rc == 6:
        assert chk.returncode == 1 and chk.stdout.startswith(f"ALERT stop_unverified since="), chk.stdout
        assert f"first={keys[0]} " in chk.stdout
    else:
        assert chk.returncode == 1 and chk.stdout.startswith(f"ALERT check_failed rc={stub_rc}"), chk.stdout
    json.loads((tmp_path / "progress.json").read_text())


def test_exec_figure_runs_even_when_the_shadow_check_fails(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 0, STUB_RC_EXEC="3", STUB_RC_SHADOW="2")
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    exp = _expected(now, last_done, 3)
    calls = _calls(tmp_path)
    assert [c for c in calls if c[0] == "exec"] == [c for c in exp if c[0] == "exec"]  # rc 3 (STOP verified): never re-run
    assert [c for c in calls if c[0] == "shadow"] == [c for c in exp if c[0] == "shadow" for _ in range(1 + RETRIES)]


def test_catch_up_does_not_redecide_a_logged_day(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 3)
    exp = _expected(now, last_done, 3)
    d1, d2 = sorted({d for k, m, d in exp if m == "day"})[:2]
    daily = tmp_path / "daily"
    (daily / "boost-day.jsonl").write_text(
        json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": d1, "exit": 0, "verdict": "no_halt"}) + "\n"
        + json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": d2, "exit": 6, "stop": {"present": False}}) + "\n")
    (daily / "exec-day.jsonl").write_text(
        json.dumps({"tool": "h5_exec_day_check", "mode": "day", "day": d1, "exit": 3, "stop": {"present": True}}) + "\n")
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _calls(tmp_path) == [c for c in exp if c[2] != d1 or c[1] == "running"]  # d1 decided by both; d2's unverified halt is re-judged
    skips = [r for r in _heartbeat(tmp_path) if r["event"] == "skip" and r["reason"] == "day_verdict_already_logged"]
    rechecked = [] if not any(m == "running" for _, m, _ in exp) else [("exec", day_of(now - 86400))]  # today's running slot: already final
    assert sorted((r["check"], r["day"]) for r in skips) == sorted([("exec", d1), ("shadow", d1)] + rechecked)


def test_stop_not_verified_gives_up_after_bounded_retries_with_backoff(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 6, H5_ENFORCE_RETRY_S="1", H5_ENFORCE_RETRIES="2", H5_ENFORCE_CHUNK_S="1")
    exp = _expected(now, last_done, 6)[:2]
    env["H5_ENFORCE_END"] = iso(next_slot(last_done) + 1)  # one slot only: the first day slot's two checks
    t0 = time.time()
    cp = run([], env, timeout=60)
    assert cp.returncode == 4, cp.stdout + cp.stderr  # END with the stop-unverified flag set
    retries = [r for r in _heartbeat(tmp_path) if r["event"] == "retry"]
    assert [r["in_s"] for r in retries] == [1, 2, 1, 2]  # 1 s, then 2 s, per check
    assert time.time() - t0 >= 6
    assert _calls(tmp_path) == [exp[0]] * 3 + [exp[1]] * 3


def test_hung_tool_is_killed_and_the_loop_goes_on(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 0, H5_ENFORCE_TOOL_TIMEOUT_S="1", H5_ENFORCE_RETRIES="0", STUB_SLEEP="30")
    cp = run([], env, timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    exp = _expected(now, last_done, 124)
    runs = [r for r in _heartbeat(tmp_path) if r["event"] == "run"]
    assert len(runs) == len(exp) and all(r["rc"] == 124 and r["verdict"] == "none" for r in runs)
    assert json.loads((tmp_path / "result.json").read_text())["metrics"]["tool_errors"] == len(exp)


def test_fresh_start_catches_up_the_executor_figure_for_every_undecided_day(tmp_path: Path) -> None:
    """No state file: the exec day check for each completed day >= FIRST_DAY whose 00:21Z slot passed, oldest first, minus decided days;
    no shadow catch-up (the session crons' job)."""
    env, now, _ = _catch_up_setup(tmp_path, 0)
    (tmp_path / "ckpt" / "h5-enforce-loop.state").unlink()
    t0 = now - now % 86400
    env["H5_ENFORCE_FIRST_DAY"] = day_of(t0 - 4 * 86400)
    days = [day_of(t0 - k * 86400) for k in (4, 3, 2)] + ([day_of(t0 - 86400)] if now % 86400 >= DAY_OFF else [])
    (tmp_path / "daily" / "exec-day.jsonl").write_text(
        json.dumps({"tool": "h5_exec_day_check", "mode": "day", "day": days[1], "exit": 5, "verdict": "unevaluated"}) + "\n")
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _calls(tmp_path) == [("exec", "day", d) for d in days if d != days[1]]
    hb = _heartbeat(tmp_path)
    assert hb[0]["resumed"] is False and hb[-1]["event"] == "end"
    cu = [r for r in hb if r["event"] == "catch_up"]
    assert len(cu) == 1 and cu[0]["from"] == days[0] and cu[0]["to"] == days[-1]
    assert [(r["check"], r["day"]) for r in hb if r["event"] == "skip"] == [("exec", days[1])]


def test_a_quit_during_the_fresh_start_catch_up_writes_no_state(tmp_path: Path) -> None:
    """rc 6 in the catch-up, then a quit: exit 4, and no state file, so the next start runs the whole catch-up again."""
    env, now, _ = _catch_up_setup(tmp_path, 6, H5_ENFORCE_RETRIES="0", STUB_QUIT_ON="exec:day",
                                  STUB_QUIT_FILE=str(tmp_path / "daily" / "enforce-loop.quit"))
    (tmp_path / "ckpt" / "h5-enforce-loop.state").unlink()
    env["H5_ENFORCE_FIRST_DAY"] = day_of(now - now % 86400 - 4 * 86400)
    cp = run([], env)
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert len(_calls(tmp_path)) == 1 and _heartbeat(tmp_path)[-1]["event"] == "quit_stop_unverified"
    assert not (tmp_path / "ckpt" / "h5-enforce-loop.state").exists()


def test_fresh_start_catch_up_is_capped_at_13_days(tmp_path: Path) -> None:
    """The executor keeps sorted(days)[-14:] including today: today-14 and its D-1 may be pruned (a false 'no entry' pair), so 13."""
    env, now, _ = _catch_up_setup(tmp_path, 0)
    (tmp_path / "ckpt" / "h5-enforce-loop.state").unlink()  # FIRST_DAY stays 2000-01-01
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 13 and all(k == "exec" and m == "day" for k, m, _ in calls)
    last = day_of(now - now % 86400 - (86400 if now % 86400 >= DAY_OFF else 2 * 86400))
    assert calls[-1][2] == last and calls[0][2] == day_of(now - now % 86400 - (13 if now % 86400 >= DAY_OFF else 14) * 86400)


def test_quit_between_the_exec_and_shadow_day_checks(tmp_path: Path) -> None:
    env, now, last_done = _catch_up_setup(tmp_path, 0, STUB_QUIT_ON="exec:day", STUB_QUIT_FILE=str(tmp_path / "daily" / "enforce-loop.quit"))
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _calls(tmp_path) == _expected(now, last_done, 0)[:1]  # the shadow day check of that slot never starts
    assert [r["event"] for r in _heartbeat(tmp_path)][-1] == "quit"


@pytest.mark.parametrize("stub_rc,loop_rc,event,alert", [
    (6, 4, "quit_stop_unverified", "ALERT quit_stop_unverified first=exec:day:"),
    (2, 0, "quit", "ALERT check_failed rc=2"),
])
def test_a_quit_never_hides_a_pending_failure(tmp_path: Path, stub_rc: int, loop_rc: int, event: str, alert: str) -> None:
    """The quit lands while a failed check waits to retry: its rc becomes last_fail; rc 6 (STOP not verified) exits 4 and alerts."""
    env, _, _ = _catch_up_setup(tmp_path, stub_rc, H5_ENFORCE_RETRY_S="30", H5_ENFORCE_CHUNK_S="1")
    p = subprocess.Popen([SH, str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    hbf = tmp_path / "daily" / "enforce-loop.jsonl"
    deadline = time.time() + 20
    while time.time() < deadline and not (hbf.exists() and '"event":"retry"' in hbf.read_text()):
        time.sleep(0.1)
    (tmp_path / "daily" / "enforce-loop.quit").write_text("")
    out, _ = p.communicate(timeout=20)
    assert p.returncode == loop_rc, out
    last = _heartbeat(tmp_path)[-1]
    assert last["event"] == event and last["pending_rc"] == stub_rc
    beat = _beat(tmp_path)
    assert beat["state"] == "exited" and beat["event"] == event and beat["last_fail_rc"] == stub_rc and beat["last_fail_s"] > 0
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["ok"] is (stub_rc != 6)
    chk = run(["--check-beat"], env)
    assert chk.returncode == 1 and chk.stdout.startswith(alert), chk.stdout


def _age_beat(tmp_path: Path, secs: int) -> None:
    beat = _beat(tmp_path)
    for k in ("at_s", "stale_at_s", "last_fail_s", "stop_unverified_s"):
        if beat.get(k):
            beat[k] -= secs
    (tmp_path / "daily" / "enforce-loop.beat").write_text(json.dumps(beat, separators=(",", ":")) + "\n")


def test_gave_up_rc6_then_quit_exits_4_and_alerts_without_time_limit(tmp_path: Path) -> None:
    """quant-proof's round-2 probe: exec day check rc 6, gave_up (no retries), then the quit file. Before: exit 0, event quit, ok:true,
    and the alert lapsed after 24 h. Now the sticky flag makes it quit_stop_unverified, exit 4, ok:false, alerting with no time limit."""
    env, now, last_done = _catch_up_setup(tmp_path, 6, H5_ENFORCE_RETRIES="0", STUB_QUIT_ON="exec:day",
                                          STUB_QUIT_FILE=str(tmp_path / "daily" / "enforce-loop.quit"))
    cp = run([], env)
    assert cp.returncode == 4, cp.stdout + cp.stderr
    hb = _heartbeat(tmp_path)
    assert [r["event"] for r in hb][-3:] == ["run", "gave_up", "quit_stop_unverified"]
    d = _expected(now, last_done, 6)[0][2]
    assert hb[-1]["pending_rc"] is None and hb[-1]["stop_unverified_keys"] == f"exec:day:{d}"
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["ok"] is False and res["metrics"]["stop_unverified_open"] == 1
    for age in (0, 90000, 30 * 86400):
        _age_beat(tmp_path, age)
        chk = run(["--check-beat"], env)
        assert chk.returncode == 1 and chk.stdout.startswith(f"ALERT quit_stop_unverified first=exec:day:{d} n=1"), chk.stdout


def test_gave_up_rc6_without_quit_alerts_past_24h_and_end_exits_4(tmp_path: Path) -> None:
    """quant-proof's second probe: rc 6, gave_up, then END after the first slot. Before: exit 0, ok:true, alert gone after 24 h."""
    env, now, last_done = _catch_up_setup(tmp_path, 6, H5_ENFORCE_RETRIES="0")
    env["H5_ENFORCE_END"] = iso(next_slot(last_done) + 1)
    cp = run([], env)
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert [r["event"] for r in _heartbeat(tmp_path)][-1] == "end"
    assert json.loads((tmp_path / "result.json").read_text())["ok"] is False
    for age in (0, 90000):
        _age_beat(tmp_path, age)
        chk = run(["--check-beat"], env)
        assert chk.returncode == 1 and chk.stdout.startswith("ALERT stop_unverified since="), chk.stdout


@pytest.mark.parametrize("seq,retries,cleared,check", [
    ("6 3", "1", True, "EXITED event=end"),          # the same check and day, retried: STOP verified present -> cleared
    ("6 0", "1", True, "EXITED event=end"),          # the same day check, retried: a final line (no halt) -> cleared
    ("6 5", "1", True, "ALERT check_failed rc=5"),   # a final line (unevaluated) clears the flag; rc 5 still gave up (24 h alert)
    ("6 6", "1", False, "ALERT stop_unverified"),    # never verified; later checks of other days (rc 3 present) do not clear it
    ("6 2", "1", False, "ALERT stop_unverified"),    # an input error is not a final line
])
def test_stop_unverified_flag_is_cleared_only_by_its_own_check_and_day(tmp_path: Path, seq: str, retries: str, cleared: bool,
                                                                      check: str) -> None:
    (tmp_path / "seq").write_text(seq)
    env, now, last_done = _catch_up_setup(tmp_path, 3, H5_ENFORCE_RETRIES=retries, STUB_RC_SEQ=str(tmp_path / "seq"))
    cp = run([], env)
    assert cp.returncode == (0 if cleared else 4), cp.stdout + cp.stderr
    d = _expected(now, last_done, 3)[0][2]
    hb = _heartbeat(tmp_path)
    assert [(r["event"], r.get("key")) for r in hb if r["event"] == "stop_verified"] == ([("stop_verified", f"exec:day:{d}")] if cleared else [])
    beat = _beat(tmp_path)
    assert (beat["stop_unverified_s"] == 0) is cleared and beat["stop_unverified_first"] == ("" if cleared else f"exec:day:{d}")
    chk = run(["--check-beat"], env)
    assert chk.returncode == (0 if check.startswith("EXITED") else 1) and chk.stdout.startswith(check), chk.stdout


def test_stop_unverified_flag_survives_a_restart(tmp_path: Path) -> None:
    """The flag is in the state file: a resumed loop keeps alerting and quits with exit 4."""
    env, now, last_done = _catch_up_setup(tmp_path, 0)
    since = now - 3600
    (tmp_path / "ckpt" / "h5-enforce-loop.state").write_text(f"{last_done} 0 0 1 0 0 {since} 6 {since} shadow:running:2026-10-12 exec:day:2026-10-11\n")
    (tmp_path / "daily" / "enforce-loop.quit").write_text("")
    cp = run([], env)
    assert cp.returncode == 4, cp.stdout + cp.stderr
    assert _heartbeat(tmp_path)[-1]["stop_unverified_keys"] == "shadow:running:2026-10-12 exec:day:2026-10-11"
    beat = _beat(tmp_path)
    assert beat["stop_unverified_s"] == since and beat["stop_unverified_n"] == 2 and beat["stop_unverified_first"] == "shadow:running:2026-10-12"


def test_quit_file_at_start_exits_0_before_any_check(tmp_path: Path) -> None:
    env, _, _ = _catch_up_setup(tmp_path, 3)
    (tmp_path / "daily" / "enforce-loop.quit").write_text("")
    cp = run([], env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _calls(tmp_path) == []
    assert [r["event"] for r in _heartbeat(tmp_path)] == ["start", "quit"]
    assert _beat(tmp_path)["state"] == "exited" and _beat(tmp_path)["event"] == "quit"
    assert "quit on request" in json.loads((tmp_path / "result.json").read_text())["summary"]
    assert (tmp_path / "daily" / "enforce-loop.quit").exists(), "the loop never deletes the quit file"
    chk = run(["--check-beat"], env)
    assert chk.returncode == 0 and chk.stdout.startswith("EXITED event=quit")


def _popen_waiting(tmp_path: Path, **extra: str) -> tuple[subprocess.Popen, dict[str, str]]:
    ex, sh = _stubs(tmp_path)
    env = env_for(tmp_path, H5_ENFORCE_TOOL=str(sh), H5_ENFORCE_EXEC_TOOL=str(ex), H5_ENFORCE_END=iso(int(time.time()) + 3 * 86400),
                  STUB_CALLS=str(tmp_path / "calls.jsonl"), REAL_EXEC=str(REAL_EXEC), **extra)
    p = subprocess.Popen([SH, str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    hbf = tmp_path / "daily" / "enforce-loop.jsonl"
    deadline = time.time() + 20
    while time.time() < deadline and not (hbf.exists() and '"event":"wait"' in hbf.read_text()):
        time.sleep(0.1)
    return p, env


def test_quit_file_while_waiting_ends_the_loop_within_a_chunk(tmp_path: Path) -> None:
    p, env = _popen_waiting(tmp_path, H5_ENFORCE_CHUNK_S="1")
    (tmp_path / "daily" / "enforce-loop.quit").write_text("")
    t0 = time.time()
    out, _ = p.communicate(timeout=20)
    assert p.returncode == 0, out
    assert time.time() - t0 < 5
    assert [r["event"] for r in _heartbeat(tmp_path)][-1] == "quit"


def test_sigterm_while_waiting_logs_and_exits_143_and_the_beat_says_waiting(tmp_path: Path) -> None:
    p, env = _popen_waiting(tmp_path)
    beat = _beat(tmp_path)
    assert beat["state"] == "waiting" and beat["event"] in ("wait", "waiting") and beat["stale_at_s"] - beat["at_s"] == 2 * 60 + 300
    assert run(["--check-beat"], env).stdout.startswith("OK state=waiting")
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=20)
    assert p.returncode == 143, out
    hb = _heartbeat(tmp_path)
    assert [r["event"] for r in hb][-2:] == ["wait", "signal"]
    assert hb[-1]["signal"] == "TERM" and hb[-1]["tool_running"] is False
    assert _calls(tmp_path) == []
    chk = run(["--check-beat"], env)
    assert chk.returncode == 1 and chk.stdout.startswith("ALERT loop_exited event=signal")


def test_sigterm_while_a_tool_runs_is_prompt_and_stops_the_tool(tmp_path: Path) -> None:
    env, _, _ = _catch_up_setup(tmp_path, 0, STUB_SLEEP="60", STUB_PID=str(tmp_path / "stub.pid"))
    p = subprocess.Popen([SH, str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline and not (tmp_path / "stub.pid").exists():
        time.sleep(0.1)
    time.sleep(0.3)
    beat = _beat(tmp_path)
    assert beat["state"] == "running_tool" and beat["stale_at_s"] - beat["at_s"] == 1800 + 30 + 300
    pid = int((tmp_path / "stub.pid").read_text())
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=20)
    assert p.returncode == 143, out
    assert time.time() - t0 < 5, "TERM must not wait for the tool"
    hb = _heartbeat(tmp_path)
    assert hb[-1]["event"] == "signal" and hb[-1]["tool_running"] is True
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the tool survived the loop's TERM")


@pytest.mark.parametrize("beat,rc,prefix", [
    (None, 1, "ALERT beat_missing"),
    ("not json", 1, "ALERT beat_unreadable"),
    ({"state": "waiting", "event": "wait", "at_s": -1000, "stale_at_s": -1, "last_fail_s": 0, "last_fail_rc": 0}, 1, "ALERT stale"),
    ({"state": "running_tool", "event": "running_tool", "at_s": -10, "stale_at_s": 600, "last_fail_s": 0, "last_fail_rc": 0}, 0, "OK state=running_tool"),
    ({"state": "waiting", "event": "alive", "at_s": -10, "stale_at_s": 600, "last_fail_s": -3600, "last_fail_rc": 6}, 1, "ALERT check_failed rc=6"),
    ({"state": "waiting", "event": "alive", "at_s": -10, "stale_at_s": 600, "last_fail_s": -90000, "last_fail_rc": 6}, 0, "OK state=waiting"),
    ({"state": "exited", "event": "error", "at_s": -10, "stale_at_s": -10, "last_fail_s": 0, "last_fail_rc": 0}, 1, "ALERT loop_exited event=error"),
    ({"state": "exited", "event": "end", "at_s": -10, "stale_at_s": -10, "last_fail_s": 0, "last_fail_rc": 0}, 0, "EXITED event=end"),
    # an unverified STOP at quit never ages out (last_fail 2 days ago still alerts)
    ({"state": "exited", "event": "quit_stop_unverified", "at_s": -10, "stale_at_s": -10, "last_fail_s": -172800, "last_fail_rc": 6}, 1,
     "ALERT quit_stop_unverified first="),
    # the sticky stop-unverified flag alerts with no time limit, in any state, also after END
    ({"state": "waiting", "event": "alive", "at_s": -10, "stale_at_s": 600, "last_fail_s": -172800, "last_fail_rc": 6,
      "stop_unverified_s": -172800, "stop_unverified_n": 1, "stop_unverified_first": "exec:day:2026-10-12"}, 1,
     "ALERT stop_unverified since="),
    ({"state": "exited", "event": "end", "at_s": -10, "stale_at_s": -10, "last_fail_s": -172800, "last_fail_rc": 6,
      "stop_unverified_s": -172800, "stop_unverified_n": 2, "stop_unverified_first": "shadow:running:2026-10-12"}, 1,
     "ALERT stop_unverified since="),
    ({"state": "waiting", "event": "alive", "at_s": -10, "stale_at_s": 600, "last_fail_s": 0, "last_fail_rc": 0,
      "stop_unverified_s": 0, "stop_unverified_n": 0, "stop_unverified_first": ""}, 0, "OK state=waiting"),
    ({"state": "waiting", "event": "alive", "at_s": -10, "stale_at_s": 600, "last_fail_s": 0, "last_fail_rc": 0,
      "stop_unverified_s": "x"}, 1, "ALERT beat_unreadable"),
])
def test_check_beat(tmp_path: Path, beat, rc: int, prefix: str) -> None:
    env = env_for(tmp_path)
    bf = Path(env["H5_ENFORCE_BEAT"])
    if beat is not None:
        bf.parent.mkdir(parents=True)
        if isinstance(beat, dict):
            now = int(time.time())
            beat = dict(beat, at_s=now + beat["at_s"], stale_at_s=now + beat["stale_at_s"],
                        last_fail_s=(now + beat["last_fail_s"]) if beat["last_fail_s"] else 0)
            if isinstance(beat.get("stop_unverified_s"), int) and beat["stop_unverified_s"]:
                beat["stop_unverified_s"] += now
            bf.write_text(json.dumps({"tool": "h5_enforce_loop", "v": 2, **beat}, separators=(",", ":")) + "\n")
        else:
            bf.write_text(beat + "\n")
    cp = run(["--check-beat"], env)
    assert cp.returncode == rc and cp.stdout.startswith(prefix), cp.stdout + cp.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
