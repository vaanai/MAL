"""Tests for scripts/ops/osb_keeper.py: synthetic temp dirs only. No real tape, no hv7, no host path."""
import builtins
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import osb_keeper as K  # noqa: E402

H = ["2026-11-02T0%d" % i for i in range(1, 6)]  # 01..05


class Env:
    def __init__(self, tmp):
        self.root = tmp / "open-sim"
        self.keep = self.root / "keep"
        self.src = tmp / "hv7"
        self.roll = tmp / "roll"
        for d in (self.root, self.src, self.roll, self.src / ".mirror"):
            d.mkdir(parents=True, exist_ok=True)
        self.events = self.src / ".mirror" / "events.jsonl"

    def log(self, hours):
        self.events.write_text("".join(json.dumps({"h": h, "kind": "closed", "ms": 1}) + "\n" for h in hours) + '{"h": "x", "kind": "open"}\nnot json\n')

    def tape(self, d, hour, suf=".jsonl"):
        p = d / ("tape-%s%s" % (hour, suf))
        p.write_bytes(b"x" * 5)
        return p

    def run(self, *args, **kw):
        out = []
        argv = ["--loop", "0"] + list(args)
        code = K.main(argv, src=str(self.src), keep=str(self.keep), backfill_allowed=str(self.roll), out=out.append, **kw)
        return code, out

    def kept(self):
        return sorted(p.name for p in self.keep.iterdir() if p.name.startswith("tape-"))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_links_only_verified_closed_hours_at_or_after_from_hour(env):
    for h in H:
        env.tape(env.src, h, ".jsonl.zst" if h == H[1] else ".jsonl")
    env.tape(env.src, "2026-11-02T00")  # before the minimum
    env.log(H[:4] + ["2026-11-02T00"])  # H[4] not verified
    code, out = env.run()
    assert code == 0
    assert env.kept() == ["tape-%s%s" % (h, ".jsonl.zst" if h == H[1] else ".jsonl") for h in H[:4]]
    for n in env.kept():
        assert os.stat(env.keep / n).st_ino == os.stat(env.src / n).st_ino and os.stat(env.keep / n).st_nlink == 2
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert st["linked_now"] == 4 and st["linked_first"] == H[0] and st["linked_last"] == H[3] and st["skipped"] == {"src_hour_not_verified": 1}
    assert st["contiguous"] and st["keep_earliest"] == H[0] and st["keep_latest"] == H[3]
    code, out = env.run()  # second pass: nothing new
    assert code == 0 and json.loads((env.keep / ".keeper-status.json").read_text())["already_present"] == 4


def test_from_hour_filters_and_minimum_refused(env):
    for h in H:
        env.tape(env.src, h)
    env.log(H)
    assert env.run("--from-hour", H[2])[0] == 0
    assert env.kept() == ["tape-%s.jsonl" % h for h in H[2:]]
    code, out = env.run("--from-hour", "2026-11-02T00")
    assert code == 2 and any("REFUSE" in o for o in out)


def test_non_tape_names_ignored(env):
    env.tape(env.src, H[0])
    for n in ("tape-%s.jsonl.part" % H[1], "trades-%s.jsonl" % H[1], "state.json", "tape-%s.jsonl.zst.tmp" % H[1], "tape-%s.txt" % H[1]):
        (env.src / n).write_bytes(b"y")
    env.log(H[:2])
    assert env.run()[0] == 0
    assert env.kept() == ["tape-%s.jsonl" % H[0]]
    assert sorted(p.name for p in env.keep.iterdir()) == [".keeper-status.json", "tape-%s.jsonl" % H[0]]


def test_never_copies_when_link_fails(env, monkeypatch):
    env.tape(env.src, H[0])
    env.log(H[:1])

    def boom(a, b, **kw):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", boom)
    code, out = env.run()
    assert code == 1 and any("KEEPER-FAIL" in o for o in out)
    assert env.kept() == []
    assert json.loads((env.keep / ".keeper-status.json").read_text())["failed_hours"] == [H[0]]


def test_symlink_in_keep_refused(env):
    t = env.tape(env.src, H[0])
    env.log(H[:1])
    env.keep.mkdir()
    os.symlink(t, env.keep / t.name)
    code, out = env.run()
    assert code == 2 and any(t.name in o for o in out)


def test_inode_mismatch_refused(env):
    t = env.tape(env.src, H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (env.keep / t.name).write_bytes(b"other")
    code, out = env.run()
    assert code == 2 and any(t.name in o for o in out)


def test_cli_has_no_src_events_keep_or_trust_flags(env):
    for flag in ("--src", "--events", "--keep", "--backfill-trust-roll"):
        with pytest.raises(SystemExit) as e:
            K.main([flag, "x", "--loop", "0"], src=str(env.src), keep=str(env.keep), out=lambda s: None)
        assert e.value.code == 2


def test_other_src_keep_events_refused_when_defaults_apply(env, tmp_path):
    env.tape(env.src, H[0])
    env.log(H[:1])
    # allow-list = the real constants, which do not exist here: every other dir is refused, nothing is created or linked
    for kw in ({"src": env.src, "keep": env.keep}, {"src": tmp_path, "keep": env.keep}):
        with pytest.raises(K.Refuse):
            K.one_pass(str(kw["src"]), str(kw["keep"]), H[0], events=str(env.events), out=lambda s: None)
    assert not env.keep.exists()
    # right src and keep but another events file
    other = tmp_path / "ev.jsonl"
    other.write_text("")
    with pytest.raises(K.Refuse):
        K.one_pass(str(env.src), str(env.keep), H[0], events=str(other), src_allowed=str(env.src), keep_allowed=str(env.keep), out=lambda s: None)
    # keep that resolves elsewhere
    os.symlink(tmp_path / "elsewhere", env.root / "lnk")
    with pytest.raises(K.Refuse):
        K.one_pass(str(env.src), str(env.root / "lnk"), H[0], src_allowed=str(env.src), keep_allowed=str(env.keep), out=lambda s: None)
    # overlap
    with pytest.raises(K.Refuse):
        K.one_pass(str(env.root), str(env.keep), H[0], src_allowed=str(env.root), keep_allowed=str(env.keep), out=lambda s: None)


def test_symlink_and_non_regular_entries_not_linked(env, tmp_path):
    target = tmp_path / "sealed-target"
    target.write_bytes(b"s")
    os.symlink(target, env.src / ("tape-%s.jsonl" % H[0]))
    env.tape(env.src, H[1])
    os.symlink(target, env.roll / ("tape-%s.jsonl" % H[2]))
    os.mkdir(env.src / ("tape-%s.jsonl" % H[3]))  # a directory with a tape name
    env.log(H[:4])
    code, out = env.run("--backfill-src", str(env.roll))
    assert code == 0
    assert env.kept() == ["tape-%s.jsonl" % H[1]]
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert st["skipped"]["not_regular_file"] == 3 and st["not_regular_files"] == ["tape-%s.jsonl" % h for h in (H[0], H[2], H[3])]
    assert any("not_regular_files" in o for o in out)


def test_link_called_without_following_symlinks(env, monkeypatch):
    env.tape(env.src, H[0])
    env.log(H[:1])
    seen = []
    real = os.link
    monkeypatch.setattr(os, "link", lambda a, b, **kw: (seen.append(kw), real(a, b, **kw))[1])
    assert env.run()[0] == 0 and seen == [{"follow_symlinks": False}]


def test_refusal_writes_status_and_oserror_does_not_end_loop(env, monkeypatch):
    t = env.tape(env.src, H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (env.keep / t.name).write_bytes(b"other")
    assert env.run()[0] == 2
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert "replaced after verification" in st["refused"] and "pass_utc" in st
    (env.keep / t.name).unlink()
    calls = []
    real = K.one_pass

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(5, "Input/output error")
        return real(*a, **kw)

    monkeypatch.setattr(K, "one_pass", flaky)
    out = []
    code = K.main(["--loop", "1"], src=str(env.src), keep=str(env.keep), sleep=lambda s: None, max_passes=3, out=out.append)
    assert code == 0 and len(calls) == 3 and any("KEEPER-FAIL pass 1" in o for o in out)
    assert env.kept() == ["tape-%s.jsonl" % H[0]]


def test_five_consecutive_oserrors_exit_4(env, monkeypatch):
    def bad(*a, **kw):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(K, "one_pass", bad)
    assert K.main(["--loop", "1"], src=str(env.src), keep=str(env.keep), sleep=lambda s: None, max_passes=50, out=lambda s: None) == 4


def test_missing_log_exit_3(env):
    assert env.run()[0] == 3


def test_backfill_links_logged_hours_only(env, tmp_path):
    old, gone, new = H[0], H[2], H[1]
    s = env.tape(env.src, new)
    os.link(s, env.roll / s.name)  # as in production: the roll holds hard links of the same inodes
    env.tape(env.roll, old)  # hv7 dropped it; the log still lists it
    env.tape(env.roll, gone)  # hv7 dropped it; the log does not list it
    env.log([old, new])
    other = tmp_path / "other-roll"
    other.mkdir()
    assert env.run("--backfill-src", str(other))[0] == 2  # not the allow-listed path
    code, out = env.run("--backfill-src", str(env.roll))
    assert code == 0 and env.kept() == ["tape-%s.jsonl" % h for h in (old, new)]
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert st["backfill_linked"] == 1 and st["backfill_not_in_log_hours"] == [gone] and st["skipped"] == {"backfill_not_in_log": 1}
    assert os.stat(env.keep / ("tape-%s.jsonl" % old)).st_ino == os.stat(env.roll / ("tape-%s.jsonl" % old)).st_ino
    assert st["contiguous"] is True and st["missing_hours"] == []


def test_backfill_not_in_log_hour_never_links(env):
    env.tape(env.roll, H[0])
    env.log([])
    assert env.run("--backfill-src", str(env.roll))[0] == 0 and env.kept() == []


def test_backfill_below_from_hour_not_linked(env):
    env.tape(env.roll, "2026-11-01T23")
    env.log(["2026-11-01T23"])
    assert env.run("--backfill-src", str(env.roll))[0] == 0
    assert env.kept() == []


def test_contiguity_report(env):
    for h in (H[0], H[1], H[3]):
        env.tape(env.src, h)
    env.log([H[0], H[1], H[3]])
    code, out = env.run()
    assert code == 0
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert st["contiguous"] is False and st["missing_hours"] == [H[2]]
    assert any("missing_hours=%s" % H[2] in o for o in out)


def test_never_opens_a_tape_file(env, monkeypatch):
    for h in H[:3]:
        env.tape(env.src, h)
    env.log(H[:3])
    real = builtins.open

    def guard(f, *a, **kw):
        if "tape-" in os.path.basename(str(f)):
            raise AssertionError("opened a tape file")
        return real(f, *a, **kw)

    monkeypatch.setattr(builtins, "open", guard)
    assert env.run()[0] == 0 and env.run()[0] == 0
    assert len(env.kept()) == 3


def test_loop_runs_n_passes_then_stops(env):
    env.tape(env.src, H[0])
    env.log(H[:1])
    sleeps = []
    out = []
    code = K.main(["--loop", "7"], src=str(env.src), keep=str(env.keep), sleep=sleeps.append, max_passes=3, out=out.append)
    assert code == 0 and sleeps == [7, 7] and sum(o.startswith("KEEPER verified_seen") for o in out) == 3


def test_loop_ends_on_refusal(env):
    env.tape(env.src, H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (env.keep / ("tape-%s.jsonl" % H[0])).write_bytes(b"z")
    sleeps = []
    assert K.main(["--loop", "7"], src=str(env.src), keep=str(env.keep), sleep=sleeps.append, max_passes=3, out=lambda s: None) == 2
    assert sleeps == []
