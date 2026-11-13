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
        argv = ["--src", str(self.src), "--keep", str(self.keep), "--loop", "0"] + list(args)
        code = K.main(argv, keep_root=str(self.root), backfill_allowed=str(self.roll), out=out.append, **kw)
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


def test_keep_outside_root_and_overlap_refused(env, tmp_path):
    env.tape(env.src, H[0])
    env.log(H[:1])
    out = []
    assert K.main(["--src", str(env.src), "--keep", str(tmp_path / "elsewhere"), "--loop", "0"], keep_root=str(env.root), out=out.append) == 2
    assert not (tmp_path / "elsewhere").exists()
    assert K.main(["--src", str(env.root), "--keep", str(env.keep), "--events", str(env.events), "--loop", "0"], keep_root=str(env.root), out=out.append) == 2
    # a symlinked keep that resolves outside the root is refused after realpath
    os.symlink(tmp_path / "elsewhere2", env.root / "lnk")
    assert K.main(["--src", str(env.src), "--keep", str(env.root / "lnk"), "--loop", "0"], keep_root=str(env.root), out=out.append) == 2


def test_missing_log_exit_3(env):
    assert env.run()[0] == 3


def test_backfill_allow_list_and_trust_modes(env, tmp_path):
    old, new = H[0], H[1]
    env.tape(env.roll, old)
    s = env.tape(env.src, new)
    os.link(s, env.roll / s.name)  # as in production: the roll holds hard links of the same inodes
    env.log([new])  # log no longer lists the old hour
    other = tmp_path / "other-roll"
    other.mkdir()
    assert env.run("--backfill-src", str(other))[0] == 2  # not the allow-listed path
    code, out = env.run("--backfill-src", str(env.roll))
    assert code == 0 and env.kept() == ["tape-%s.jsonl" % new]
    assert json.loads((env.keep / ".keeper-status.json").read_text())["skipped"] == {"backfill_not_in_log": 1}
    code, out = env.run("--backfill-src", str(env.roll), "--backfill-trust-roll")
    assert code == 0 and env.kept() == ["tape-%s.jsonl" % h for h in (old, new)]
    st = json.loads((env.keep / ".keeper-status.json").read_text())
    assert st["backfill_trust_roll_linked"] == 1 and any("trust_roll_linked=1" in o for o in out)
    assert os.stat(env.keep / ("tape-%s.jsonl" % old)).st_ino == os.stat(env.roll / ("tape-%s.jsonl" % old)).st_ino


def test_backfill_logged_old_hour_links_without_trust(env):
    env.tape(env.roll, H[0])
    env.log(H[:1])  # hour is in the log but gone from src
    code, out = env.run("--backfill-src", str(env.roll))
    assert code == 0 and env.kept() == ["tape-%s.jsonl" % H[0]]
    assert json.loads((env.keep / ".keeper-status.json").read_text())["backfill_trust_roll_linked"] == 0


def test_backfill_below_from_hour_not_linked(env):
    env.tape(env.roll, "2026-11-01T23")
    env.log([])
    assert env.run("--backfill-src", str(env.roll), "--backfill-trust-roll")[0] == 0
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
    code = K.main(["--src", str(env.src), "--keep", str(env.keep), "--loop", "7"], keep_root=str(env.root), sleep=sleeps.append, max_passes=3, out=out.append)
    assert code == 0 and sleeps == [7, 7] and sum(o.startswith("KEEPER verified_seen") for o in out) == 3


def test_loop_ends_on_refusal(env):
    env.tape(env.src, H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (env.keep / ("tape-%s.jsonl" % H[0])).write_bytes(b"z")
    sleeps = []
    assert K.main(["--src", str(env.src), "--keep", str(env.keep), "--loop", "7"], keep_root=str(env.root), sleep=sleeps.append, max_passes=3, out=lambda s: None) == 2
    assert sleeps == []
