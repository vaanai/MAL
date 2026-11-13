"""Tests for scripts/ops/r4b_declog_copy.py: synthetic temp dirs and a local fake remote only. No real declog, no ssh."""
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import r4b_declog_copy as C  # noqa: E402

CID = "r4bt-a2-dry-1113a"
DAY = "2026-11-16.jsonl"


class Fake:
    def __init__(self):
        self.files = {}
        self.sent = []
        self.corrupt = False
        self.fail_stat = False
        self.truncate = False

    def stat(self, cid, day):
        if self.fail_stat:
            raise C.TransportError("down")
        d = self.files.get((cid, day))
        return None if d is None else (len(d), hashlib.sha256(d).hexdigest())

    def send(self, cid, day, path, n, sha):
        if self.truncate:  # the connection dropped: the remote verified length/sha before the rename and kept the old file
            raise C.TransportError("send rc=1")
        with open(path, "rb") as f:
            data = f.read(n)
        assert hashlib.sha256(data).hexdigest() == sha
        self.sent.append((cid, day, len(data)))
        self.files[(cid, day)] = data[:-1] + b"X" if self.corrupt else data


class Env:
    def __init__(self, tmp, cid=CID):
        self.parent = tmp / "r4b-dry"
        self.cid = cid
        self.root = self.parent / cid / "declog"
        self.arm = self.root / "decisions" / "r4b-exp027"
        self.arm.mkdir(parents=True)
        self.status = tmp / "status" / "status.json"
        self.fake = Fake()

    def day(self, name=DAY, data=b'{"a":1}\n{"b":2}\n'):
        (self.arm / name).write_bytes(data)

    def run(self, *args, series=None, now=C.P0_COUNT_INSTANT_S + 1):
        out = []
        s = series if series is not None else ["%s=%s" % (self.cid, self.root)]
        argv = ["--loop", "0"] + [x for it in s for x in ("--series", it)] + list(args)
        code = C.main(argv, send_fn=self.fake.send, remote_stat_fn=self.fake.stat, allowed_parents=(str(self.parent),), status_path=str(self.status), out=out.append, now_fn=lambda: now)
        return code, out


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_torn_tail_cut_and_new_then_unchanged(env):
    env.day(data=b"AAA\nBBB\nCC")
    code, out = env.run()
    assert code == 0 and env.fake.files[(CID, DAY)] == b"AAA\nBBB\n"
    assert any(" new" in o for o in out)
    code, out = env.run()
    assert code == 0 and len(env.fake.sent) == 1 and any("unchanged" in o for o in out)


def test_only_torn_bytes_nothing_sent(env):
    env.day(data=b"no newline yet")
    assert env.run()[0] == 0 and env.fake.sent == []


def test_extended_prefix_sent_and_verified(env):
    env.day(data=b"AAA\n")
    env.run()
    env.day(data=b"AAA\nBBB\nCC")
    code, out = env.run()
    assert code == 0 and env.fake.files[(CID, DAY)] == b"AAA\nBBB\n" and len(env.fake.sent) == 2 and any("extended" in o for o in out)


def test_no_flock_no_json_parse(env, monkeypatch):
    env.day(data=b"not json at all\n{broken\n")

    def boom(*a, **k):
        raise AssertionError("forbidden")

    monkeypatch.setattr(fcntl, "flock", boom)
    monkeypatch.setattr(fcntl, "lockf", boom)
    monkeypatch.setattr(json, "loads", boom)
    monkeypatch.setattr(json, "load", boom)
    assert env.run()[0] == 0 and env.fake.sent


def test_divergence_exit4_nothing_sent(env):
    env.day(data=b"AAA\nBBB\n")
    env.fake.files[(CID, DAY)] = b"ZZZ\n"
    code, out = env.run()
    assert code == 4 and env.fake.sent == [] and any("stop and ask QP" in o for o in out)
    env.fake.files[(CID, DAY)] = b"AAA\nBBB\nCCC\n"  # remote longer than local
    assert env.run()[0] == 4 and env.fake.sent == []


def test_remote_sha_mismatch_exit1(env):
    env.day()
    env.fake.corrupt = True
    code, out = env.run()
    assert code == 1 and any("VERIFY-FAIL" in o for o in out)


def test_transport_failure_exit1_and_five_consecutive(env):
    env.day()
    env.fake.fail_stat = True
    assert env.run()[0] == 1
    out, sleeps = [], []
    code = C.main(["--loop", "3", "--series", "%s=%s" % (CID, env.root)], send_fn=env.fake.send, remote_stat_fn=env.fake.stat, allowed_parents=(str(env.parent),),
                  status_path=str(env.status), sleep=sleeps.append, out=out.append, now_fn=lambda: C.P0_COUNT_INSTANT_S + 1)
    assert code == 1 and len(sleeps) == 4


@pytest.mark.parametrize("cid", ["dry-long-1101a", "r4bt-a1-1113a", "r4bt-a1-dry-1113a", "r4bt-a2-dry-1113b", "r4bt-a2-1113", "r4bt-a2-1113a/../x", "r4bt-a4-1113a", "r4bt-a2-dry-1113a\n"])
def test_change_id_refused(tmp_path, cid):
    e = Env(tmp_path, cid="x")
    code, out = e.run(series=["%s=%s" % (cid, e.root)])
    assert code == 2 and e.fake.sent == []


@pytest.mark.parametrize("cid", ["r4bt-a2-dry-1113a", "r4bt-a2-1114b", "r4bt-a3-1114c", "r4bt-a3-dry-1114c"])
def test_change_id_allowed(tmp_path, cid):
    e = Env(tmp_path, cid=cid)
    e.day()
    assert e.run("--quiet-until", "2026-11-25T00:00:00Z")[0] == 0 and e.fake.sent


def test_root_must_be_allowed_parent_and_end_in_declog(env, tmp_path):
    other = tmp_path / "elsewhere" / CID / "declog"
    other.mkdir(parents=True)
    assert env.run(series=["%s=%s" % (CID, other)])[0] == 2
    assert env.run(series=["%s=%s" % (CID, env.root.parent)])[0] == 2
    assert env.run(series=["%s=%s" % (CID, env.root / "decisions")])[0] == 2
    assert env.run(series=["%s=%s" % (CID, tmp_path)])[0] == 2


def test_denylisted_component_in_root(tmp_path):
    e = Env(tmp_path)
    bad = tmp_path / "r4b-dry" / CID / "out" / "declog"
    bad.mkdir(parents=True)
    assert e.run(series=["%s=%s" % (CID, bad)])[0] == 2


def test_denylist_names_and_out_dir_never_opened(env, monkeypatch):
    env.day()
    for n in ("host.log", "m1-start.txt", "sidecar.jsonl", "2026-11-16.jsonl.bak", "x.jsonl"):
        (env.arm / n).write_text("secret-row\n")
    (env.root / "out").mkdir()
    (env.root / "out" / DAY).write_text("zzz\n")
    opened = []
    real = os.open

    def spy(p, *a, **k):
        opened.append(str(p))
        return real(p, *a, **k)

    monkeypatch.setattr(os, "open", spy)
    assert env.run()[0] == 0
    assert [os.path.basename(p) for p in opened if "status.json" not in p] == [DAY]
    assert list(env.fake.files) == [(CID, DAY)]


def test_symlinked_day_file_refused(env, tmp_path):
    target = tmp_path / "t.jsonl"
    target.write_bytes(b"A\n")
    os.symlink(target, env.arm / DAY)
    code, out = env.run()
    assert code == 2 and env.fake.sent == []


def test_day_file_outside_arm_dir_not_read(env):
    env.day()
    (env.root / "decisions" / "2026-11-17.jsonl").write_bytes(b"A\n")
    (env.root / "2026-11-18.jsonl").write_bytes(b"A\n")
    assert env.run()[0] == 0
    assert list(env.fake.files) == [(CID, DAY)]


def test_symlinked_arm_dir_refused(env, tmp_path):
    real = tmp_path / "realarm"
    real.mkdir()
    (real / DAY).write_bytes(b"A\n")
    import shutil
    shutil.rmtree(env.arm)
    os.symlink(real, env.arm)
    assert env.run()[0] == 2 and env.fake.sent == []


def test_output_and_status_have_only_allowed_fields(env):
    env.day(data=b"row-content-1\nrow-content-2\n")
    code, out = env.run()
    text = "\n".join(out)
    assert "row-content" not in text and "lines" not in text and "rows" not in text
    st = json.loads(env.status.read_text())
    assert set(st) == {"pass_utc", "dry_run", "exit", "files"}
    assert set(st["files"][0]) == {"change_id", "file", "bytes", "sha256", "state"}
    assert st["files"][0]["bytes"] == 28 and st["files"][0]["sha256"] == hashlib.sha256(b"row-content-1\nrow-content-2\n").hexdigest()
    assert "row-content" not in env.status.read_text()


def test_dry_run_sends_nothing(env):
    env.day()
    env.fake.fail_stat = True  # would fail if asked
    code, out = env.run("--dry-run")
    assert code == 0 and env.fake.sent == [] and any("dry-run" in o for o in out)


def test_remote_command_and_argv_strings():
    assert C.ssh_argv() == ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3", "mal-research-0"]
    sha = "ab" * 32
    send = C.remote_send_cmd(CID, DAY, 28, sha)
    d = "/data2/mal/r4b-preview/declog-copy/%s/decisions/r4b-exp027" % CID
    t = "%s/.%s.tmp.$$" % (d, DAY)
    script = ("umask 077; mkdir -p %s && cat > %s && [ \"$(wc -c < %s | tr -d ' ')\" = 28 ] && [ \"$(sha256sum < %s | cut -d' ' -f1)\" = %s ] && mv %s %s/%s || { rm -f %s; exit 1; }"
              % (d, t, t, t, sha, t, d, DAY, t))
    assert send == "sh -c " + __import__("shlex").quote(script)
    assert C.build_argv(send) == C.ssh_argv() + [send]
    st = C.remote_stat_cmd(CID, DAY)
    assert st.startswith("sh -c ") and "sha256sum" in st and d in st


def test_remote_command_refuses_unvalidated_names():
    for cid, day in (("x; rm -rf /", DAY), (CID, "../x.jsonl"), (CID, "a b.jsonl"), ("r4bt-a1-1113a", DAY), (CID, "2026-11-16.jsonl; id")):
        with pytest.raises(C.Refuse):
            C.remote_send_cmd(cid, day, 1, "a" * 64)
        with pytest.raises(C.Refuse):
            C.remote_stat_cmd(cid, day)


def test_remote_send_cmd_rejects_bad_length_or_sha():
    for n, sha in ((-1, "a" * 64), (True, "a" * 64), ("5", "a" * 64), (5, "a" * 63), (5, "A" * 64), (5, "a" * 64 + "; id")):
        with pytest.raises(C.Refuse):
            C.remote_send_cmd(CID, DAY, n, sha)


def test_real_transport_uses_argv_and_stdin_without_executing(env):
    env.day(data=b"A\nBB\n")
    path = str(env.arm / DAY)
    calls = []

    class R:
        returncode = 0
        stdout = b""

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return R()

    assert C.ssh_remote_stat(CID, DAY, run=fake_run) is None
    R.stdout = ("5 " + "a" * 64 + "\n").encode()
    assert C.ssh_remote_stat(CID, DAY, run=fake_run) == (5, "a" * 64)
    R.stdout = b"garbage\n"
    with pytest.raises(C.TransportError):
        C.ssh_remote_stat(CID, DAY, run=fake_run)
    R.stdout = b""
    R.returncode = 255
    with pytest.raises(C.TransportError):
        C.ssh_remote_stat(CID, DAY, run=fake_run)

    class P:
        def __init__(self, argv, **kw):
            self.argv, self.kw, self.buf, self.rc = argv, kw, [], 0
            self.stdin = self

        def write(self, b):
            self.buf.append(b)

        def close(self):
            pass

        def wait(self, timeout=None):
            return self.rc

        def kill(self):
            pass

    made = []

    def popen(argv, **kw):
        made.append(P(argv, **kw))
        return made[-1]

    n, sha = C.prefix_info(path)
    C.ssh_send(CID, DAY, path, n, sha, popen=popen)
    assert made[0].argv == C.build_argv(C.remote_send_cmd(CID, DAY, n, sha)) and b"".join(made[0].buf) == b"A\nBB\n"

    def bad_popen(argv, **kw):
        p = P(argv, **kw)
        p.rc = 1
        return p

    with pytest.raises(C.TransportError):
        C.ssh_send(CID, DAY, path, n, sha, popen=bad_popen)


def _run_script(cmd, data):
    import shlex
    import subprocess
    argv = shlex.split(cmd)
    assert argv[:2] == ["sh", "-c"]
    return subprocess.run(["/bin/sh", "-c", argv[2]], input=data, capture_output=True, timeout=30)


def _dest(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DEST_ROOT", str(tmp_path / "dest"))
    return tmp_path / "dest" / CID / "decisions" / "r4b-exp027"


def test_real_sh_full_stdin_installs_0600_dirs_0700(tmp_path, monkeypatch):
    d = _dest(tmp_path, monkeypatch)
    data = b"AAA\nBBB\n"
    r = _run_script(C.remote_send_cmd(CID, DAY, len(data), hashlib.sha256(data).hexdigest()), data)
    assert r.returncode == 0 and (d / DAY).read_bytes() == data and sorted(os.listdir(d)) == [DAY]
    assert oct((d / DAY).stat().st_mode & 0o777) == "0o600" and oct(d.stat().st_mode & 0o777) == "0o700"


def test_real_sh_truncated_stdin_keeps_the_good_file_and_leaves_no_tmp(tmp_path, monkeypatch):
    d = _dest(tmp_path, monkeypatch)
    old = b"AAA\n"
    new = b"AAA\nBBB\nCCC\n"
    assert _run_script(C.remote_send_cmd(CID, DAY, len(old), hashlib.sha256(old).hexdigest()), old).returncode == 0
    cmd = C.remote_send_cmd(CID, DAY, len(new), hashlib.sha256(new).hexdigest())
    r = _run_script(cmd, new[:7])  # cut short
    assert r.returncode != 0 and (d / DAY).read_bytes() == old and sorted(os.listdir(d)) == [DAY]
    r = _run_script(cmd, new[:-1] + b"X")  # right length, wrong bytes
    assert r.returncode != 0 and (d / DAY).read_bytes() == old and sorted(os.listdir(d)) == [DAY]
    assert _run_script(cmd, new).returncode == 0 and (d / DAY).read_bytes() == new


def test_truncated_transfer_keeps_good_file_exit1_next_pass_repairs(env):
    env.day(data=b"AAA\n")
    env.run()
    env.day(data=b"AAA\nBBB\n")
    env.fake.truncate = True
    code, out = env.run()
    assert code == 1 and env.fake.files[(CID, DAY)] == b"AAA\n"
    env.fake.truncate = False
    code, out = env.run()
    assert code == 0 and env.fake.files[(CID, DAY)] == b"AAA\nBBB\n"


# ---------------------------------------------------------------- B3: quiet log
def test_quiet_stdout_before_the_instant_has_no_count_proxy(env):
    env.day(name="2026-11-16.jsonl", data=b"x" * 123456 + b"\n")
    env.day(name="2026-11-17.jsonl", data=b"y\n")
    code, out = env.run(now=C.P0_COUNT_INSTANT_S - 1)
    text = "\n".join(out)
    sha = hashlib.sha256(b"y\n").hexdigest()
    assert code == 0 and text.splitlines() == ["R4B-COPY pass 1 start", "R4B-COPY pass 1 end exit 0"]
    assert "2026-11" not in text and "bytes" not in text and sha not in text and "new" not in text and "123457" not in text
    st = json.loads(env.status.read_text())
    assert {f["file"] for f in st["files"]} == {"2026-11-16.jsonl", "2026-11-17.jsonl"} and {f["bytes"] for f in st["files"]} == {123457, 2}
    assert oct(env.status.stat().st_mode & 0o777) == "0o600" and oct(env.status.parent.stat().st_mode & 0o777) == "0o700"


def test_quiet_failure_lines_name_change_id_and_kind_only(env):
    env.day(name="2026-11-16.jsonl", data=b"AAA\n")
    env.fake.files[(CID, "2026-11-16.jsonl")] = b"ZZZ\n"
    code, out = env.run(now=C.P0_COUNT_INSTANT_S - 1)
    text = "\n".join(out)
    assert code == 4 and "DIVERGED %s" % CID in text and "2026-11-16" not in text
    env.fake.files.clear()
    env.fake.corrupt = True
    code, out = env.run(now=C.P0_COUNT_INSTANT_S - 1)
    text = "\n".join(out)
    assert code == 1 and "VERIFY-FAIL %s" % CID in text and "2026-11-16" not in text
    env.fake.corrupt, env.fake.fail_stat = False, True
    code, out = env.run(now=C.P0_COUNT_INSTANT_S - 1)
    assert code == 1 and "TRANSPORT-FAIL %s" % CID in "\n".join(out) and "2026-11-16" not in "\n".join(out)


def test_quiet_dry_run_also_quiet(env):
    env.day()
    code, out = env.run("--dry-run", now=C.P0_COUNT_INSTANT_S - 1)
    assert [o for o in out if DAY in o or "bytes" in o] == [] and json.loads(env.status.read_text())["files"][0]["state"] == "dry-run"


def test_refusal_lines_carry_no_day_name(env):
    os.symlink(env.arm.parent, env.arm / "2026-11-16.jsonl")
    code, out = env.run(now=C.P0_COUNT_INSTANT_S - 1)
    assert code == 2 and "2026-11-16" not in "\n".join(out)


def test_quiet_until_argument_rules(tmp_path):
    e = Env(tmp_path)
    e.day()
    assert e.run("--quiet-until", "2026-11-18T00:00:00Z")[0] == 2  # earlier than the a2 instant, for an a2 series
    assert e.run("--quiet-until", "garbage")[0] == 2
    code, out = e.run("--quiet-until", "2026-12-04T00:00:00Z")  # later: stays quiet at the default `now`
    assert code == 0 and "bytes" not in "\n".join(out) and DAY not in "\n".join(out)
    a3 = Env(tmp_path / "x", cid="r4bt-a3-1114c")
    a3.day()
    assert a3.run()[0] == 2  # an a3 series needs its own instant
    assert a3.run("--quiet-until", "2026-11-25T00:00:00Z")[0] == 0


def test_instant_constant():
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(C.P0_COUNT_INSTANT_S, timezone.utc).isoformat() == "2026-11-19T00:10:00+00:00"


# ---------------------------------------------------------------- other failure paths
def test_decisions_symlink_refused(env, tmp_path):
    real = tmp_path / "realdec"
    (real / "r4b-exp027").mkdir(parents=True)
    (real / "r4b-exp027" / DAY).write_bytes(b"A\n")
    import shutil
    shutil.rmtree(env.root / "decisions")
    os.symlink(real, env.root / "decisions")
    assert env.run()[0] == 2 and env.fake.sent == []


def test_status_file_after_transport_failure_has_exit_1(env):
    env.day()
    env.fake.fail_stat = True
    assert env.run()[0] == 1
    st = json.loads(env.status.read_text())
    assert st["exit"] == 1 and st["files"] == []


def test_empty_remote_file_is_not_a_missing_file(env):
    env.day(data=b"AAA\n")
    env.fake.files[(CID, DAY)] = b""
    code, out = env.run()
    assert code == 0 and any("extended" in o for o in out) and env.fake.files[(CID, DAY)] == b"AAA\n"


def test_file_growing_between_listing_and_read(env, monkeypatch):
    env.day(data=b"AAA\n")
    real = C.prefix_info

    def grow(path):
        with open(path, "ab") as f:
            f.write(b"BBB\nCC")  # appended after the listing, last row torn
        return real(path)

    monkeypatch.setattr(C, "prefix_info", grow)
    assert env.run()[0] == 0 and env.fake.files[(CID, DAY)] == b"AAA\nBBB\n"


def test_big_file_streamed_with_newline_far_from_the_end(env, monkeypatch):
    monkeypatch.setattr(C, "CHUNK", 8)
    env.day(data=b"AAAAAAAAAAAAAAAAAAAAAAAA\nBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    assert env.run()[0] == 0 and env.fake.files[(CID, DAY)] == b"AAAAAAAAAAAAAAAAAAAAAAAA\n"


def test_two_series_one_pass(tmp_path):
    e = Env(tmp_path)
    e.day()
    cid2 = "r4bt-a2-1114b"
    arm2 = tmp_path / "r4b-dry" / cid2 / "declog" / "decisions" / "r4b-exp027"
    arm2.mkdir(parents=True)
    (arm2 / DAY).write_bytes(b"Q\n")
    code, _ = e.run(series=["%s=%s" % (CID, e.root), "%s=%s" % (cid2, arm2.parents[1])])
    assert code == 0 and sorted(e.fake.files) == sorted([(CID, DAY), (cid2, DAY)])
