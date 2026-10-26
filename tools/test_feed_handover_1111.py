"""Tests for scripts/research/feed-handover-1111.sh (the FALLBACK plain restart at H' #1652's end) on a FAKE feed process (no real tape, no network, no MiScusi).

Every test runs the script in test mode (FH_TEST=1) against a temp tape dir. The script refuses the production tape path in test mode, and it only
matches processes whose argv has "--out-dir <that temp dir>", so the real research-0 feed can never be matched or signalled from here.
The fake feed (tools/arena_feed.py in a temp git repo) takes the same flock as the real one, rewrites arena-feed-status.json every 0.1 s, stops
cleanly on SIGTERM (or ignores it with FAKE_IGNORE_TERM=1) and, like the real feed, writes one process_restart gap row when it finds a previous
status file.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "research" / "feed-handover-1111.sh"
SCRIPT_1107 = ROOT / "scripts" / "research" / "feed-handover-1107.sh"

pytestmark = pytest.mark.skipif(not Path("/proc/locks").exists() or shutil.which("pgrep") is None, reason="needs Linux /proc and pgrep")

FAKE_FEED = r'''
import argparse, fcntl, json, os, signal, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--out-dir", required=True)
a, _ = ap.parse_known_args()
out = a.out_dir
os.makedirs(out, exist_ok=True)
st = os.path.join(out, "arena-feed-status.json")
prev = None
if os.path.exists(st):
    with open(st) as f:
        prev = json.load(f)["updated_ms"]
if os.environ.get("FAKE_NO_LOCK") != "1":
    fd = os.open(os.path.join(out, ".arena-feed.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(3)
stop = []
def on_term(*_):
    if os.environ.get("FAKE_IGNORE_TERM") != "1":
        stop.append(1)
signal.signal(signal.SIGTERM, on_term)
def status():
    tmp = st + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"updated_ms": int(time.time() * 1000), "pid": os.getpid()}, f)
    os.replace(tmp, st)
now = int(time.time() * 1000)
if prev is not None and os.environ.get("FAKE_NO_GAP") != "1":
    hour = time.strftime("%Y-%m-%dT%H", time.gmtime(now / 1000))
    with open(os.path.join(out, "tape-%s.jsonl" % hour), "a") as f:
        f.write(json.dumps({"type": "gap", "prog": "all", "kind": "process_restart", "data_loss": True, "from_ms": min(prev, now),
                            "to_ms": now, "clean_stop": True, "torn_tail": False, "prev_pid": None}) + "\n")
status()
while not stop:
    status()
    time.sleep(0.1)
status()
sys.exit(0)
'''

WRAPPER = '#!/bin/sh\ncase "$1" in feed) ;; *) exit 2;; esac\nexec "$FAKE_PY" -m tools.arena_feed --out-dir "$FH_TAPE" --ws-url fake\n'


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.repo = tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "tools" / "__init__.py").write_text("")
        (self.repo / "tools" / "arena_feed.py").write_text(FAKE_FEED)
        g = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "fake"], check=True)
        self.head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        self.wrapper = tmp / "wrapper.sh"
        self.wrapper.write_text(WRAPPER)
        self.tape = tmp / "tape"
        self.tape.mkdir()
        self.state = tmp / "state"
        self.procs: list[subprocess.Popen] = []
        now = int(time.time())
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "FH_TEST": "1",
            "FH_TAPE": str(self.tape), "FH_STATE": str(self.state), "FH_WRAPPER": str(self.wrapper), "FH_WRAPPER_SHA": sha256(self.wrapper),
            "FH_HEAD": self.head, "FH_FEED_SHA": sha256(self.repo / "tools" / "arena_feed.py"),
            "FH_T_START": str(now + 1), "FH_T_END": str(now + 120), "FH_MIN_END": str(now), "FH_LIMIT_SEC": "1209600",
            "FH_CHECK_A": str(now), "FH_CHECK_B": str(now), "FH_PY": sys.executable, "FAKE_PY": sys.executable,
        }

    def feed(self, tape: Path | None = None, **extra: str) -> subprocess.Popen:
        env = dict(self.env, **extra)
        p = subprocess.Popen([sys.executable, "-m", "tools.arena_feed", "--out-dir", str(tape or self.tape), "--ws-url", "fake"], cwd=self.repo, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def wait_lock(self, pid: int, tape: Path | None = None, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if pid in lock_holders((tape or self.tape) / ".arena-feed.lock"):
                return
            time.sleep(0.05)
        raise AssertionError(f"pid {pid} never took the lock")

    def run(self, mode: str, timeout: float = 60.0, cwd: Path | None = None, **extra: str) -> tuple[int, str]:
        env = dict(self.env, **extra)
        r = subprocess.run(["sh", str(SCRIPT), mode], cwd=cwd or self.repo, env=env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def start(self, mode: str, **extra: str) -> tuple[subprocess.Popen, Path]:
        """Run the script in the background (handover execs into the feed, so it does not return)."""
        log = self.tmp / f"{mode}-{len(self.procs)}.log"
        env = dict(self.env, **extra)
        p = subprocess.Popen(["sh", str(SCRIPT), mode], cwd=self.repo, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p, log

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(5)


def lock_holders(lock: Path) -> set[int]:
    if not lock.exists():
        return set()
    ino = str(os.stat(lock).st_ino)
    out = set()
    for line in Path("/proc/locks").read_text().splitlines():
        f = line.split()
        if len(f) >= 6 and f[1] == "FLOCK" and f[5].split(":")[-1] == ino:
            out.add(int(f[4]))
    return out


def wait_until(cond, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.cleanup()


def marker(r: Rig) -> dict[str, str]:
    return dict(l.split("=", 1) for l in (r.state / "HANDOVER_DONE").read_text().splitlines() if "=" in l)


# ---- the pinned instants -------------------------------------------------------------------------------------------------------------------------

def test_production_pins_match_the_iso_instants():
    text = SCRIPT.read_text()
    pins = dict(re.findall(r"^(P_[A-Z_]+)=(\S+)", text, re.M))
    utc = lambda s: int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())  # noqa: E731
    assert int(pins["P_T_START"]) == utc("2026-11-11T22:00:00Z")
    assert int(pins["P_T_END"]) == utc("2026-11-11T22:08:00Z")
    assert int(pins["P_CHECK_A"]) == utc("2026-11-11T22:09:00Z")
    assert int(pins["P_CHECK_B"]) == utc("2026-11-12T01:00:00Z")
    assert int(pins["P_MIN_END"]) == utc("2026-11-18T22:00:00Z")
    assert int(pins["P_LIMIT_SEC"]) == 14 * 86400
    assert int(pins["P_MIN_END"]) - int(pins["P_LIMIT_SEC"]) == utc("2026-11-04T22:00:00Z")  # the earliest submit
    # after HV6's last in-clock instant (its read, 11-11T13:00Z); the window, check A before H' #1652's limit end (about 11-12T00:30Z, per the
    # manager; confirm from job status), check B after it
    hv6_last, h_end = utc("2026-11-11T13:00:00Z"), utc("2026-11-12T00:30:00Z")
    assert hv6_last < int(pins["P_T_START"]) < int(pins["P_T_END"]) < int(pins["P_CHECK_A"]) < h_end < int(pins["P_CHECK_B"])
    assert pins["P_HEAD"] == "f5bd26e779746f40692a760a2b6a5d4e0cadc0b7"
    assert pins["P_FEED_SHA"] == "7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f"
    assert pins["P_WRAPPER_SHA"] == "3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205"
    assert pins["P_TAPE"] == "/data/mal/arena/tape"
    assert pins["P_STATE"] == "/data/mal/hunt-1008/ops/feed-handover-1111"
    assert pins["P_TERM_WAIT_S"] == "60"


def test_only_the_pins_differ_from_the_merged_1107_script():
    """The fallback is the merged, QP-ruled 1107 code with new instants: every line after the header equals 1107's except the pin lines and the tag."""
    def body(p):
        t = p.read_text()
        return t[t.index("set -u\n"):].splitlines()
    a, b = body(SCRIPT_1107), body(SCRIPT)
    assert len(a) == len(b)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    allowed = ("P_T_START=", "P_T_END=", "P_CHECK_A=", "P_CHECK_B=", "P_MIN_END=", "P_STATE=", 'ME="fh11', '  ME="fh11', 'case "$-" in *x*)')
    assert diff and all(y.startswith(allowed) for _, y in diff), [y for _, y in diff if not y.startswith(allowed)]
    assert len(diff) == 9


def test_a_v2_writer_holding_the_lock_is_never_touched(rig):
    """If the v2 takeover already ran, the lock holder is `-m tools.arena_feed_v2`: the fallback matches no v1 feed, refuses, and signals nothing."""
    (rig.repo / "tools" / "arena_feed_v2.py").write_text(FAKE_FEED)  # untracked: the clean-checkout pin ignores untracked files
    env = dict(rig.env)
    p = subprocess.Popen([sys.executable, "-m", "tools.arena_feed_v2", "--out-dir", str(rig.tape), "--ws-url", "fake"], cwd=rig.repo, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rig.procs.append(p)
    rig.wait_lock(p.pid)
    for extra in ({}, {"FH_START_IF_NONE": "1"}):
        rc, out = rig.run("handover", **extra)
        assert rc == 13 and "lock is held" in out, out
        assert p.poll() is None
    assert not (rig.state / "HANDOVER_DONE").exists()

def test_posix_sh_syntax():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    if shutil.which("dash"):
        assert subprocess.run(["dash", "-n", str(SCRIPT)]).returncode == 0


# ---- refusals before anything is signalled ------------------------------------------------------------------------------------------------------

def test_production_mode_refuses_any_override(tmp_path):
    env = {"PATH": os.environ["PATH"], "FH_TAPE": str(tmp_path)}
    r = subprocess.run(["sh", str(SCRIPT), "dryrun"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "override FH_TAPE is set without FH_TEST=1" in r.stdout


def test_production_mode_outside_pinned_checkout_refuses_on_pins(tmp_path):
    r = subprocess.run(["sh", str(SCRIPT), "handover"], cwd=tmp_path, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
    assert r.returncode == 10 and "FEED HANDOVER REFUSED" in r.stderr


def test_test_mode_refuses_production_tape(rig):
    rc, out = rig.run("dryrun", FH_TAPE="/data/mal/arena/tape")
    assert rc == 2 and "refuses the production tape" in out


def test_bad_mode():
    r = subprocess.run(["sh", str(SCRIPT), "go"], capture_output=True, text=True)
    assert r.returncode == 2


def test_pin_mismatches_refuse(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    assert rig.run("handover", FH_HEAD="0" * 40)[0] == 10
    assert rig.run("handover", FH_WRAPPER_SHA="0" * 64)[0] == 10
    assert rig.run("handover", FH_FEED_SHA="0" * 64)[0] == 10
    (rig.repo / "tools" / "__init__.py").write_text("# dirty\n")
    rc, out = rig.run("handover")
    assert rc == 10 and "tracked changes" in out
    assert old.poll() is None and not (rig.state / "HANDOVER_DONE").exists()


def test_refuses_two_feed_processes(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    twin = rig.feed(FAKE_NO_LOCK="1")
    time.sleep(0.5)
    rc, out = rig.run("handover")
    assert rc == 12 and "2 arena_feed processes" in out
    assert old.poll() is None and twin.poll() is None


def test_refuses_after_window_end(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    now = int(time.time())
    rc, out = rig.run("handover", FH_T_START=str(now - 600), FH_T_END=str(now - 10))
    assert rc == 11 and "after the window end" in out
    assert old.poll() is None and not (rig.state / "HANDOVER_DONE").exists()


def test_refuses_when_submitted_too_early_for_the_limit(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    now = int(time.time())
    rc, out = rig.run("handover", FH_MIN_END=str(now + 1209600 + 3600))
    assert rc == 15 and "submit at or after" in out
    assert old.poll() is None


def test_refuses_feed_that_does_not_hold_the_lock(rig):
    (rig.tape / ".arena-feed.lock").write_text("")
    f = rig.feed(FAKE_NO_LOCK="1")
    time.sleep(0.5)
    rc, out = rig.run("handover")
    assert rc == 13 and "lock holders" in out
    assert f.poll() is None


def test_refuses_missing_tape_or_lock(rig):
    rc, out = rig.run("handover", FH_TAPE=str(rig.tmp / "nope"))
    assert rc == 13 and "missing" in out
    rc, out = rig.run("handover")  # tape exists, no lock file
    assert rc == 13 and "lock file" in out


def test_refuses_zero_feeds_unless_start_if_none(rig):
    (rig.tape / ".arena-feed.lock").write_text("")
    rc, out = rig.run("handover")
    assert rc == 12 and "no arena_feed process" in out
    p, log = rig.start("handover", FH_START_IF_NONE="1")
    wait_until(lambda: p.pid in lock_holders(rig.tape / ".arena-feed.lock"))
    m = marker(rig)
    assert m["old_pid"] == "" and m["new_pid"] == str(p.pid) and m["start_if_none"] == "1"


# ---- the hand-off -------------------------------------------------------------------------------------------------------------------------------

def test_handover_happy_path_then_check(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    other_tape = rig.tmp / "other-tape"
    bystander = rig.feed(tape=other_tape); rig.wait_lock(bystander.pid, tape=other_tape)
    time.sleep(0.3)
    p, log = rig.start("handover")
    # the job execs into the feed: the new feed has the job's own PID and holds the tape lock
    wait_until(lambda: p.pid in lock_holders(rig.tape / ".arena-feed.lock"))
    assert old.wait(10) == 0  # clean stop on SIGTERM
    assert bystander.poll() is None  # another out-dir is never touched
    text = log.read_text()
    assert "SIGTERM sent to %d" % old.pid in text and "EXEC new feed in this process" in text and "WAKE" in text
    assert "SIGKILL" not in text and "REFUSED" not in text
    m = marker(rig)
    assert m["old_pid"] == str(old.pid) and m["new_pid"] == str(p.pid) and m["sigkill"] == "0"
    with open(f"/proc/{p.pid}/cmdline", "rb") as f:
        assert b"tools.arena_feed" in f.read()
    # post-check (check A and B immediately)
    time.sleep(0.3)
    rc, out = rig.run("check")
    assert rc == 0, out
    assert "CHECK_OK" in out and "count=1" in out and "exec chain kept the PID" in out
    span = int(re.search(r"span_ms=(\d+)", out).group(1))
    assert span < 5000  # stop + start of the fake feed: the gap is small
    # a second run of the hand-off job (MiScusi resume) with the feed running refuses: never a second feed
    rc, out = rig.run("handover")
    assert rc == 12 and "not starting a second feed" in out
    assert p.poll() is None


def test_sigkill_only_after_term_wait_and_disclosed(rig):
    old = rig.feed(FAKE_IGNORE_TERM="1"); rig.wait_lock(old.pid)
    p, log = rig.start("handover", FH_TERM_WAIT_S="1", FH_KILL_WAIT_S="5")
    wait_until(lambda: p.pid in lock_holders(rig.tape / ".arena-feed.lock"))
    assert old.wait(10) == -signal.SIGKILL
    text = log.read_text()
    assert "DISCLOSE: old feed %d still alive 1 s after SIGTERM: SIGKILL" % old.pid in text
    assert marker(rig)["sigkill"] == "1"


def test_resume_after_handover_execs_without_window(rig):
    rig.state.mkdir()
    (rig.state / "HANDOVER_DONE").write_text("old_pid=1\nold_start=1\nnew_pid=2\nexec_epoch=%d\n" % int(time.time()))
    (rig.tape / ".arena-feed.lock").write_text("")
    now = int(time.time())
    p, log = rig.start("handover", FH_T_START=str(now - 7200), FH_T_END=str(now - 3600))
    wait_until(lambda: p.pid in lock_holders(rig.tape / ".arena-feed.lock"))
    text = log.read_text()
    assert "UNPLANNED restart" in text
    assert "resume_pid=%d" % p.pid in (rig.state / "HANDOVER_DONE").read_text()


def test_dryrun_signals_nothing(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    rc, out = rig.run("dryrun")
    assert rc == 0 and "DRYRUN_OK old_pid=%d" % old.pid in out and "window: not open yet" in out
    assert old.poll() is None and not rig.state.exists()


# ---- the post-check -----------------------------------------------------------------------------------------------------------------------------

def test_check_fails_without_marker(rig):
    rc, out = rig.run("check")
    assert rc == 16 and "no marker" in out


def test_check_fails_with_no_feed_or_two_restart_rows(rig):
    old = rig.feed(); rig.wait_lock(old.pid)
    p, _ = rig.start("handover")
    wait_until(lambda: p.pid in lock_holders(rig.tape / ".arena-feed.lock"))
    hour = time.strftime("%Y-%m-%dT%H", time.gmtime(int(marker(rig)["exec_epoch"])))
    with open(rig.tape / f"tape-{hour}.jsonl", "a") as f:
        f.write(json.dumps({"type": "gap", "kind": "process_restart", "from_ms": 1, "to_ms": 2}) + "\n")
    rc, out = rig.run("check")
    assert rc == 16 and "expected exactly 1" in out
    p.terminate(); p.wait(10)
    rc, out = rig.run("check")
    assert rc == 16 and "feed processes=0, expected 1" in out
