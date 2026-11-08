"""Tests for tools/floor_tip_linkdir.py and scripts/ops/floor-tip-linkdir.sh. Temp dirs and a fake ssh (runs the remote command locally); no network."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import floor_tip_linkdir as fl

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000  # fixed clock
H_NOW = fl.hour_name(NOW)


def hh(back: int) -> str:
    return fl.hour_name(NOW - back * 3600)


@pytest.fixture
def env(tmp_path):
    src, dst, rem = tmp_path / "src", tmp_path / "dst", tmp_path / "rem"
    for d in (src, rem):
        d.mkdir()
    fake = tmp_path / "fakessh"
    fake.write_text('#!/bin/sh\nshift\nexec sh -c "$1"\n')
    fake.chmod(0o755)
    return src, dst, rem, fake


def mk(env, **kw):
    src, dst, rem, fake = env
    d = dict(retain_h=240, backfill_h=10, min_free_pct=1, remote_dir=str(rem), ssh_prefix=[str(fake)], free_path=str(dst.parent), now=lambda: NOW)
    d.update(kw)
    return fl.Linker(src, dst, **d)


def snap(d: Path):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir())


def test_open_hour_link_sees_appends(env):
    src, dst, _, _ = env
    f = src / f"tape-{H_NOW}.jsonl"
    f.write_text("a\n")
    lk = mk(env)
    assert lk.sync_once()["link"] == 1
    d = dst / f.name
    assert os.stat(d).st_ino == os.stat(f).st_ino
    with open(f, "a") as fh:
        fh.write("b\n")
    assert d.read_text() == "a\nb\n"
    assert lk.sync_once()["link"] == 0


def test_close_order_zst_linked_before_jsonl_removed(env, monkeypatch):
    src, dst, _, _ = env
    h = hh(1)
    (src / f"tape-{h}.jsonl").write_text("x\n")
    lk = mk(env)
    lk.sync_once()
    assert (dst / f"tape-{h}.jsonl").exists()
    (src / f"tape-{h}.jsonl.zst").write_bytes(b"zz")
    os.unlink(src / f"tape-{h}.jsonl")
    order = []
    real_link, real_unlink = os.link, os.unlink
    monkeypatch.setattr(os, "link", lambda a, b, *k, **w: (order.append(("link", Path(b).name)), real_link(a, b, *k, **w))[1])
    monkeypatch.setattr(os, "unlink", lambda p, *k, **w: (order.append(("unlink", Path(p).name)), real_unlink(p, *k, **w))[1])
    acts = lk.sync_once()
    assert acts["close"] == 1
    assert order == [("link", f"tape-{h}.jsonl.zst"), ("unlink", f"tape-{h}.jsonl")]
    assert (dst / f"tape-{h}.jsonl.zst").exists() and not (dst / f"tape-{h}.jsonl").exists()
    # the old open file, still linked in the writer dir? gone; dst .zst shares the inode
    assert os.stat(dst / f"tape-{h}.jsonl.zst").st_ino == os.stat(src / f"tape-{h}.jsonl.zst").st_ino


def test_both_present_at_source_links_zst_only(env):
    src, dst, _, _ = env
    h = hh(1)
    (src / f"tape-{h}.jsonl").write_text("x\n")
    (src / f"tape-{h}.jsonl.zst").write_bytes(b"zz")
    mk(env).sync_once()
    assert sorted(p.name for p in dst.iterdir() if p.name.startswith("tape-")) == [f"tape-{h}.jsonl.zst"]


def test_retention_by_hour_name_dst_only(env):
    src, dst, _, _ = env
    old, keep = hh(241), hh(239)
    dst.mkdir()
    (dst / f"tape-{old}.jsonl.zst").write_bytes(b"1")
    (dst / f"tape-{keep}.jsonl.zst").write_bytes(b"2")
    (src / f"tape-{H_NOW}.jsonl").write_text("a\n")
    before = snap(src)
    lk = mk(env)
    assert lk.sync_once()["prune"] == 1
    names = {p.name for p in dst.iterdir()}
    assert f"tape-{old}.jsonl.zst" not in names and f"tape-{keep}.jsonl.zst" in names
    assert snap(src) == before


def test_backfill_skips_present_and_verifies_sha(env):
    src, dst, rem, _ = env
    (src / f"tape-{hh(5)}.jsonl.zst").write_bytes(b"s")  # earliest SRC hour = now-5
    for b in range(10, 4, -1):  # remote has now-10 .. now-5
        (rem / f"tape-{hh(b)}.jsonl.zst").write_bytes(f"data{b}".encode())
    dst.mkdir()
    (dst / f"tape-{hh(8)}.jsonl.zst").write_bytes(b"mine")  # present: must not be refetched
    lk = mk(env)
    assert lk.plan_backfill() == [hh(10), hh(9), hh(7), hh(6)]
    lk.sync_once()
    assert lk.backfill() == 4
    assert (dst / f"tape-{hh(8)}.jsonl.zst").read_bytes() == b"mine"
    assert (dst / f"tape-{hh(9)}.jsonl.zst").read_bytes() == b"data9"
    assert hh(5) not in lk.plan_backfill() and not list(dst.glob("*.part"))
    assert lk.backfilled == 4
    lk.write_status()
    import json
    st = json.loads((dst / ".linkdir" / "status.json").read_text())
    assert st["backfilled"] == 4 and st["earliest"] == hh(10) and st["latest"] == hh(5) and st["errors"] == []


def test_backfill_sha_mismatch_keeps_nothing(env):
    src, dst, rem, fake = env
    (src / f"tape-{hh(5)}.jsonl.zst").write_bytes(b"s")
    (rem / f"tape-{hh(6)}.jsonl.zst").write_bytes(b"good")
    # an ssh that lies about sha256sum
    liar = fake.parent / "liarssh"
    liar.write_text('#!/bin/sh\nshift\ncase "$1" in sha256sum*) echo ' + "0" * 64 + ' x;; *) exec sh -c "$1";; esac\n')
    liar.chmod(0o755)
    lk = mk(env, ssh_prefix=[str(liar)])
    lk.sync_once()
    assert lk.backfill() == 0
    assert not (dst / f"tape-{hh(6)}.jsonl.zst").exists() and not list(dst.glob("*.part"))
    assert lk.errors and "mismatch" in lk.errors[-1]["e"]


def test_backfill_refuses_low_free_space(env):
    src, dst, rem, _ = env
    (src / f"tape-{hh(5)}.jsonl.zst").write_bytes(b"s")
    (rem / f"tape-{hh(6)}.jsonl.zst").write_bytes(b"good")
    lk = mk(env, min_free_pct=101)
    lk.sync_once()
    assert lk.backfill() == 0
    assert not (dst / f"tape-{hh(6)}.jsonl.zst").exists()
    assert "disk_reserve" in lk.errors[-1]["e"]


def test_never_writes_source_dir(env):
    src, dst, rem, _ = env
    (src / f"tape-{hh(5)}.jsonl.zst").write_bytes(b"s")
    (src / f"tape-{H_NOW}.jsonl").write_text("a\n")
    (rem / f"tape-{hh(6)}.jsonl.zst").write_bytes(b"good")
    before = snap(src)
    mode, dir_mt = stat.S_IMODE(src.stat().st_mode), src.stat().st_mtime_ns
    src.chmod(0o555)  # a write attempt would raise (unless root)
    try:
        lk = mk(env)
        lk.sync_once()
        lk.backfill()
        lk.write_status()
    finally:
        src.chmod(mode)
    assert snap(src) == before and src.stat().st_mtime_ns == dir_mt


def test_dry_plan_writes_nothing(env, capsys):
    src, dst, rem, _ = env
    (src / f"tape-{H_NOW}.jsonl").write_text("a\n")
    lk = mk(env, dry=True)
    assert lk.sync_once()["link"] == 1
    lk.backfill()
    lk.write_status()
    assert not dst.exists()


def test_main_refuses_dst_inside_src(env):
    src, _, _, _ = env
    assert fl.main(["--src", str(src), "--dst", str(src / "sub")]) == 2


def test_shell_script_dry_and_refusals(env, tmp_path):
    src, dst, _, _ = env
    home = tmp_path / "home"
    (home / "mal-data").mkdir(parents=True)
    base = {"PATH": os.environ["PATH"], "HOME": str(home), "DRY": "1", "SRC": str(src), "DST": str(home / "mal-data/floor-tip/tape")}
    r = subprocess.run(["sh", str(ROOT / "scripts/ops/floor-tip-linkdir.sh")], env=base, capture_output=True, text=True)
    assert r.returncode == 0 and "DRY=1" in r.stdout and "sync plan" in r.stdout
    assert not (home / "mal-data/floor-tip").exists()
    bad = dict(base, DST=str(home / "mal-data/arena-tipfeed/tape"))
    r = subprocess.run(["sh", str(ROOT / "scripts/ops/floor-tip-linkdir.sh")], env=bad, capture_output=True, text=True)
    assert r.returncode == 2 and "REFUSED" in r.stdout
