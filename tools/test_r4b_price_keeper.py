"""Tests for scripts/ops/r4b_price_keeper.py: synthetic temp dirs only. No real tape, no hv7, no host path."""
import builtins
import calendar
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import r4b_price_keeper as K  # noqa: E402

H = ["2026-11-15T22", "2026-11-15T23", "2026-11-16T00", "2026-11-16T01", "2026-11-16T02"]
SHA = "a" * 64
ZST = ".jsonl.zst"


def hour_end(h):
    return calendar.timegm((int(h[0:4]), int(h[5:7]), int(h[8:10]), int(h[11:13]) + 1, 0, 0))


NOW = hour_end("2026-11-16T02") + 600  # 10 min after the last test hour closed


class Env:
    def __init__(self, tmp):
        self.keep = tmp / "root" / "r4b-price-keep"
        self.src = tmp / "hv7"
        (self.src / ".mirror").mkdir(parents=True)
        self.keep.parent.mkdir(parents=True)
        self.events = self.src / ".mirror" / "events.jsonl"

    def log(self, hours, **over):
        rows = []
        for h in hours:
            r = {"h": h, "kind": "closed", "ms": 1, "zst_bytes": 5, "zst_sha": SHA}
            r.update(over)
            rows.append(json.dumps(r) + "\n")
        self.events.write_text("".join(rows) + '{"h": "x", "kind": "open"}\nnot json\n')

    def tape(self, hour, suf=ZST, size=5):
        p = self.src / ("tape-%s%s" % (hour, suf))
        p.write_bytes(b"x" * size)
        return p

    def run(self, *args, now=NOW):
        out = []
        code = K.main(["--loop", "0"] + list(args), src=str(self.src), keep=str(self.keep), out=out.append, now_fn=lambda: now)
        return code, out

    def kept(self):
        return sorted(p.name for p in self.keep.iterdir() if p.name.startswith("tape-"))

    def status(self):
        return json.loads((self.keep / ".keeper-status.json").read_text())

    def hours_rows(self):
        return [json.loads(x) for x in (self.keep / ".keeper-hours.jsonl").read_text().splitlines()]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def z(h):
    return "tape-%s%s" % (h, ZST)


def test_links_only_verified_closed_hours_from_floor(env):
    for h in H:
        env.tape(h)
    env.tape("2026-11-15T21")
    env.log(H[:3] + ["2026-11-15T21"])  # 21 is before the floor, H[3:] are not verified
    code, _ = env.run()
    assert code == 0
    assert env.kept() == [z(h) for h in H[:3]]
    st = env.status()
    assert st["linked_now"] == 3 and st["contiguous"] and st["missing_hours"] == [] and st["waiting_for_floor"] is False
    assert os.stat(env.keep / z(H[0])).st_ino == os.stat(env.src / z(H[0])).st_ino


def test_floor_refusal(env):
    env.tape(H[0])
    env.log(H[:1])
    code, out = env.run("--from-hour", "2026-11-15T21")
    assert code == 2 and any("KEEPER-REFUSE" in o for o in out)


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
    code, _ = env.run()
    assert code == 0 and len(env.kept()) == 3 and env.status()["missing_hours"] == []


def test_symlink_and_non_regular_skipped(env, tmp_path):
    real = tmp_path / "elsewhere"
    real.write_bytes(b"y")
    os.symlink(real, env.src / z(H[0]))
    env.tape(H[1])
    env.log(H[:2])
    code, out = env.run()
    assert env.kept() == [z(H[1])]
    st = env.status()
    assert st["not_regular_files"] == [z(H[0])] and st["skipped"]["not_regular_file"] == 1 and st["missing_hours"] == [H[0]]
    assert any("not_regular_files" in o for o in out)


def test_inode_mismatch_refused(env):
    p = env.tape(H[0])
    env.log(H[:1])
    assert env.run()[0] == 0
    p.unlink()
    env.tape(H[0])  # replaced after verification, same size
    code, out = env.run()
    assert code == 2 and any("stop and ask QP" in o for o in out)


def test_symlink_in_keep_refused(env, tmp_path):
    env.tape(H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (tmp_path / "t").write_bytes(b"z")
    os.symlink(tmp_path / "t", env.keep / z(H[1]))
    assert env.run()[0] == 2


def test_non_zst_entry_in_keep_refused(env):
    env.tape(H[0])
    env.log(H[:1])
    env.keep.mkdir()
    (env.keep / ("tape-%s.jsonl" % H[1])).write_bytes(b"x")
    assert env.run()[0] == 2


# ---------------------------------------------------------------- B1: alerts
def test_hole_at_floor_hour_alert_and_exit5(env):
    for h in H[1:3]:  # 22 absent from src, 23 and 24 (H[1], H[2]) linkable
        env.tape(h)
    env.log(H[:3])
    code, out = env.run("--fail-on-missing")
    assert code == 5 and env.kept() == [z(H[1]), z(H[2])]  # later hours were still linked in the same pass
    assert "R4B-KEEPER MISSING 1 hour(s): %s" % H[0] in out
    st = env.status()
    assert st["missing_hours"] == [H[0]] and st["waiting_for_floor"] is False and st["alert"] is True


def test_hole_without_flag_is_loud_only(env):
    for h in H[1:3]:
        env.tape(h)
    env.log(H[:3])
    code, out = env.run()
    assert code == 0 and "R4B-KEEPER MISSING 1 hour(s): %s" % H[0] in out


def test_verified_but_unlinkable_is_not_waiting(env):
    env.log(H[:1])  # verified at the floor, file not in src
    code, out = env.run("--fail-on-missing")
    st = env.status()
    assert code == 5 and st["waiting_for_floor"] is False and st["missing_hours"] == [H[0]]


def test_hour_never_verified_inside_span_is_missing(env):
    env.tape(H[1])
    env.log([H[1]])  # 22 never verified, 23 verified
    code, out = env.run("--fail-on-missing")
    assert code == 5 and env.status()["missing_hours"] == [H[0]] and env.status()["waiting_for_floor"] is False


def test_only_hours_before_floor_verified_is_waiting(env):
    env.tape("2026-11-15T20")
    env.log(["2026-11-15T20"])
    code, out = env.run("--fail-on-missing", now=hour_end("2026-11-15T20") + 60)
    st = env.status()
    assert code == 0 and st["waiting_for_floor"] is True and st["missing_hours"] == [] and env.kept() == []
    assert not any("MISSING" in o or "STALE" in o for o in out)


def test_stale_receiver_warns_then_fails(env):
    env.tape(H[0])
    env.log(H[:1])
    code, out = env.run("--fail-on-missing", now=hour_end(H[0]) + 7 * 3600)
    st = env.status()
    assert code == 0 and st["lag_h"] == 7.0 and st["stale"] is True and st["newest_verified_hour"] == H[0]
    assert "R4B-KEEPER STALE lag_h=7.0" in out
    code, out = env.run("--fail-on-missing", now=hour_end(H[0]) + 80 * 3600)
    assert code == 5 and "R4B-KEEPER STALE lag_h=80.0" in out
    assert env.run(now=hour_end(H[0]) + 80 * 3600)[0] == 0  # without the flag only loud


def test_not_stale_quiet(env):
    env.tape(H[0])
    env.log(H[:1])
    code, out = env.run("--fail-on-missing", now=hour_end(H[0]) + 600)
    assert code == 0 and not any("STALE" in o for o in out) and env.status()["stale"] is False


def test_link_failure_does_not_stop_later_hours_exit5_after_pass(env, monkeypatch):
    for h in H[:3]:
        env.tape(h)
    env.log(H[:3])
    real = os.link

    def flaky(a, b, **kw):
        if H[0] in a:
            raise OSError(5, "boom")
        return real(a, b, **kw)

    monkeypatch.setattr(os, "link", flaky)
    code, out = env.run("--fail-on-missing")
    assert code == 5 and env.kept() == [z(H[1]), z(H[2])] and env.status()["failed_hours"] == [H[0]]
    code, _ = env.run()
    assert code == 1  # without the flag: retried next pass
    monkeypatch.undo()
    assert env.run("--fail-on-missing")[0] == 0 and len(env.kept()) == 3


def test_verified_hour_not_yet_in_src_is_reported(env):
    env.tape(H[0])
    env.log(H[:2])
    code, out = env.run()
    assert env.status()["missing_hours"] == [H[1]] and env.status()["skipped"]["not_in_src"] == 1


# ---------------------------------------------------------------- B2: zst only, size, sha list
def test_size_mismatch_hour_refused_loudly_and_missing(env):
    env.tape(H[0], size=7)  # event says 5
    env.tape(H[1])
    env.log(H[:2])
    code, out = env.run("--fail-on-missing")
    st = env.status()
    assert code == 5 and env.kept() == [z(H[1])] and st["size_mismatch_hours"] == [H[0]] and st["missing_hours"] == [H[0]]
    assert any("KEEPER-REFUSE-HOUR" in o for o in out)


def test_keep_entry_size_checked_every_pass(env):
    env.tape(H[0])
    env.log(H[:1])
    assert env.run()[0] == 0
    env.log(H[:1], zst_bytes=9)
    code, out = env.run()
    assert code == 2 and any("zst_bytes" in o for o in out)


def test_jsonl_only_closed_hour_not_linked(env):
    env.tape(H[0], suf=".jsonl")
    env.tape(H[1])
    env.log(H[:2])
    code, out = env.run("--fail-on-missing")
    st = env.status()
    assert code == 5 and env.kept() == [z(H[1])] and st["live_jsonl_beside_closed_hours"] == [H[0]] and st["missing_hours"] == [H[0]]


def test_jsonl_beside_zst_links_the_zst_only(env):
    env.tape(H[0], suf=".jsonl", size=99)
    env.tape(H[0])
    env.log(H[:1])
    assert env.run()[0] == 0 and env.kept() == [z(H[0])]


def test_incomplete_closed_event_not_linked(env):
    env.tape(H[0])
    env.log(H[:1], zst_sha="short")
    code, _ = env.run()
    assert env.kept() == [] and env.status()["incomplete_event_hours"] == [H[0]]


def test_hours_list_carries_event_sha_and_is_append_only(env):
    for h in H[:3]:
        env.tape(h)
    env.log(H[:2])
    env.run()
    env.log(H[:3], zst_sha="b" * 64)  # later event rows must not rewrite earlier hours
    env.run()
    env.run()
    rows = env.hours_rows()
    assert [r["h"] for r in rows] == H[:3]
    assert rows[0]["zst_sha"] == SHA and rows[2]["zst_sha"] == "b" * 64
    r = rows[0]
    assert r["name"] == z(H[0]) and r["zst_bytes"] == 5 and r["inode"] == os.stat(env.keep / z(H[0])).st_ino and r["dev"] == os.stat(env.keep / z(H[0])).st_dev
    assert oct(os.stat(env.keep / ".keeper-hours.jsonl").st_mode & 0o777) == "0o600"
    assert oct(os.stat(env.keep).st_mode & 0o777) == "0o700"


def test_first_closed_row_per_hour_wins(env):
    env.tape(H[0])
    rows = [{"h": H[0], "kind": "closed", "ms": 9, "zst_bytes": 99, "zst_sha": "c" * 64}, {"h": H[0], "kind": "closed", "ms": 3, "zst_bytes": 5, "zst_sha": SHA}]
    env.events.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert env.run()[0] == 0 and env.hours_rows()[0]["zst_sha"] == SHA


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

    real_os_open = os.open

    def guard_os(p, *a, **kw):
        if "tape-" in os.path.basename(str(p)):
            raise AssertionError("opened a tape file")
        return real_os_open(p, *a, **kw)

    monkeypatch.setattr(builtins, "open", guard)
    monkeypatch.setattr(os, "open", guard_os)
    assert env.run()[0] == 0 and env.run()[0] == 0
    assert len(env.kept()) == 3


def test_loop_runs_n_passes_then_stops(env):
    env.tape(H[0])
    env.log(H[:1])
    sleeps, out = [], []
    code = K.main(["--loop", "7"], src=str(env.src), keep=str(env.keep), sleep=sleeps.append, max_passes=3, out=out.append, now_fn=lambda: NOW)
    assert code == 0 and sleeps == [7, 7]


def test_cli_roots_are_the_constants():
    assert K.DEFAULT_SRC == "/data2/mal/arena-tip-feed/hv7" and K.DEFAULT_KEEP == "/data2/mal/scratch/r4b-price-keep" and K.FLOOR_HOUR == "2026-11-15T22"


def test_wrong_roots_refused(env):
    with pytest.raises(K.Refuse):
        K.one_pass(str(env.src), str(env.keep), K.FLOOR_HOUR, "/data2/mal/arena-tip-feed/hv7", "/data2/mal/scratch/r4b-price-keep", lambda *_: None)
