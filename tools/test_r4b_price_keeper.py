"""Tests for scripts/ops/r4b_price_keeper.py: synthetic temp dirs only. No real tape, no hv7, no host path."""
import builtins
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import r4b_price_keeper as K  # noqa: E402

H = ["2026-11-15T22", "2026-11-15T23", "2026-11-16T00", "2026-11-16T01", "2026-11-16T02"]


class Env:
    def __init__(self, tmp):
        self.keep = tmp / "root" / "r4b-price-keep"
        self.src = tmp / "hv7"
        (self.src / ".mirror").mkdir(parents=True)
        self.keep.parent.mkdir(parents=True)
        self.events = self.src / ".mirror" / "events.jsonl"

    def log(self, hours):
        self.events.write_text("".join(json.dumps({"h": h, "kind": "closed", "ms": 1}) + "\n" for h in hours) + '{"h": "x", "kind": "open"}\nnot json\n')

    def tape(self, hour, suf=".jsonl"):
        p = self.src / ("tape-%s%s" % (hour, suf))
        p.write_bytes(b"x" * 5)
        return p

    def run(self, *args):
        out = []
        code = K.main(["--loop", "0"] + list(args), src=str(self.src), keep=str(self.keep), out=out.append)
        return code, out

    def kept(self):
        return sorted(p.name for p in self.keep.iterdir() if p.name.startswith("tape-"))

    def status(self):
        return json.loads((self.keep / ".keeper-status.json").read_text())


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_links_only_verified_closed_hours_from_floor(env):
    for h in H:
        env.tape(h)
    env.tape("2026-11-15T21")
    env.log(H[:3] + ["2026-11-15T21"])  # 21 is before the floor, H[3:] are not verified
    code, _ = env.run()
    assert code == 0
    assert env.kept() == ["tape-%s.jsonl" % h for h in H[:3]]
    st = env.status()
    assert st["linked_now"] == 3 and st["contiguous"] and st["missing_hours"] == [] and st["waiting_for_floor"] is False
    assert os.stat(env.keep / ("tape-%s.jsonl" % H[0])).st_ino == os.stat(env.src / ("tape-%s.jsonl" % H[0])).st_ino


def test_floor_refusal(env):
    env.tape(H[0])
    env.log(H[:1])
    code, out = env.run("--from-hour", "2026-11-15T21")
    assert code == 2 and any("KEEPER-REFUSE" in o for o in out)
    assert not any(p.name.startswith("tape-") for p in env.keep.iterdir()) if env.keep.exists() else True


def test_no_backfill_flag(env):
    with pytest.raises(SystemExit):
        env.run("--backfill-src", "/x")


def test_idempotent_and_no_prune(env):
    for h in H[:3]:
        env.tape(h)
    env.log(H[:3])
    env.run()
    for p in list(env.src.glob("tape-*")):
        p.unlink()  # hv7 pruned the hours
    env.log([])
    code, _ = env.run()
    assert code == 0 and len(env.kept()) == 3


def test_symlink_and_non_regular_skipped(env, tmp_path):
    real = tmp_path / "elsewhere"
    real.write_bytes(b"y")
    os.symlink(real, env.src / ("tape-%s.jsonl" % H[0]))
    env.tape(H[1])
    env.log(H[:2])
    code, out = env.run()
    assert code == 0 and env.kept() == ["tape-%s.jsonl" % H[1]]
    st = env.status()
    assert st["not_regular_files"] == ["tape-%s.jsonl" % H[0]] and st["skipped"]["not_regular_file"] == 1
    assert any("not_regular_files" in o for o in out)


def test_inode_mismatch_refused(env):
    p = env.tape(H[0])
    env.log(H[:1])
    assert env.run()[0] == 0
    p.unlink()
    env.tape(H[0])  # replaced after verification
    code, out = env.run()
    assert code == 2 and any("stop and ask QP" in o for o in out)


def test_symlink_in_keep_refused(env, tmp_path):
    env.tape(H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (tmp_path / "t").write_bytes(b"z")
    os.symlink(tmp_path / "t", env.keep / ("tape-%s.jsonl" % H[1]))
    assert env.run()[0] == 2


def test_missing_hour_alert_status_and_exit5(env):
    for h in (H[0], H[1], H[3]):
        env.tape(h)
    env.log([H[0], H[1]])
    assert env.run("--fail-on-missing")[0] == 0
    env.log([H[0], H[1], H[2], H[3]])  # H[2] verified but its file is not in src
    code, out = env.run("--fail-on-missing")
    assert code == 5
    assert "R4B-KEEPER MISSING 1 hour(s): %s" % H[2] in out
    assert env.status()["missing_hours"] == [H[2]] and env.status()["contiguous"] is False
    code, out = env.run()  # without the flag the pass is only loud
    assert code == 0 and "R4B-KEEPER MISSING 1 hour(s): %s" % H[2] in out


def test_unverified_hole_counts_as_missing(env):
    env.tape(H[0])
    env.tape(H[2])
    env.log([H[0], H[2]])
    code, out = env.run("--fail-on-missing")
    assert code == 5 and env.status()["missing_hours"] == [H[1]]


def test_waiting_for_floor_is_not_an_error(env):
    env.tape("2026-11-15T20")
    env.log(["2026-11-15T20"])
    code, out = env.run("--fail-on-missing")
    st = env.status()
    assert code == 0 and st["waiting_for_floor"] is True and st["missing_hours"] == [] and env.kept() == []
    assert not any("MISSING" in o for o in out)


def test_log_unreadable_exit3(env):
    code, _ = env.run()
    assert code == 3


def test_no_tape_file_is_ever_opened(env, monkeypatch):
    for h in H[:3]:
        env.tape(h)
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
    env.tape(H[0])
    env.log(H[:1])
    sleeps, out = [], []
    code = K.main(["--loop", "7"], src=str(env.src), keep=str(env.keep), sleep=sleeps.append, max_passes=3, out=out.append)
    assert code == 0 and sleeps == [7, 7]


def test_cli_roots_are_the_constants():
    assert K.DEFAULT_SRC == "/data2/mal/arena-tip-feed/hv7" and K.DEFAULT_KEEP == "/data2/mal/scratch/r4b-price-keep" and K.FLOOR_HOUR == "2026-11-15T22"


def test_wrong_roots_refused(env, tmp_path):
    out = []
    with pytest.raises(K.Refuse):
        K.one_pass(str(env.src), str(env.keep), K.FLOOR_HOUR, "/data2/mal/arena-tip-feed/hv7", "/data2/mal/scratch/r4b-price-keep", out.append)
