"""tools/tape_mirror.py on SYNTHETIC tape files in tmp dirs (no real tape, no network, no ssh): the serve pass, the puller's checks, the closed-hour
install, retention, the disk reserve, the fail-closed status reader, and end-to-end runs with serve as a subprocess (live tail, compaction, restart,
a killed session), including tools/arena_state reading the mirror exactly as it reads the source."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest

from tools import tape_mirror as tm
from tools.arena_state import ArenaState
from tools.arena_zst import compact_file

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "tape_mirror.py"
SOL = 1_000_000_000
MINT = "MINT" + "1" * 40
pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required")


def hk(off_h: int) -> str:
    return tm.hour_key(tm.now_ms() + off_h * tm.H_MS)


def row(t: int, i: int) -> bytes:
    """A synthetic bonding trade in the arena tape format (as tools/test_arena_state.curve)."""
    q = 30 * SOL
    r = {"v": 1, "type": "trade", "prog": "pumpfun", "slot": 1000 + i, "t_recv_ms": t, "signature": f"sig{i}", "event_index": 0, "venue": "pump_bonding",
         "mint": MINT, "trader": f"T{i % 7}", "side": "buy" if i % 3 else "sell", "sol_lamports": SOL // 10, "token_raw": 1_000_000, "quote_reserve": q,
         "base_reserve": int(q / SOL / 1e-7 * 1_000_000), "creator": "C" * 44}
    return (json.dumps(r) + "\n").encode()


def append_rows(d: Path, h: str, i0: int, n: int) -> int:
    t0 = tm.hour_ms(h) + 1000
    with open(d / tm.open_name(h), "ab") as fh:
        for i in range(i0, i0 + n):
            fh.write(row(t0 + i, i))
    return i0 + n


def frames(data: bytes) -> list[tuple[dict, bytes]]:
    out, i = [], 0
    while i < len(data):
        j = data.index(b"\n", i)
        hdr = json.loads(data[i:j])
        i = j + 1
        n = hdr["n"] if hdr["k"] in ("d", "z") else 0
        out.append((hdr, data[i:i + n]))
        i += n
    return out


def server(src: Path, since: str | None = None, have=None, closed=None, **kw) -> tuple[tm.Server, list]:
    sink: list[bytes] = []
    srv = tm.Server(src, since or hk(-10), dict(have or {}), set(closed or ()), write=sink.append, **kw)
    return srv, sink


def drain(sink: list) -> list[tuple[dict, bytes]]:
    data = b"".join(sink)
    sink.clear()
    return frames(data)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def zst_bytes(raw: bytes, tmp: Path) -> bytes:
    p = tmp / "x.jsonl"
    p.write_bytes(raw)
    out = subprocess.run(["zstd", "-q", "-c", str(p)], check=True, capture_output=True).stdout
    p.unlink()
    return out


@pytest.fixture
def mk(tmp_path):
    made = []

    def make(dst: Path | None = None, cmd=("true",), **kw) -> tm.Puller:
        pl = tm.Puller(dst or tmp_path / "dst", list(cmd), **kw)
        made.append(pl)
        return pl

    yield make
    for pl in made:
        pl.close()


# ---- serve -------------------------------------------------------------------------------------------------------------------
def test_serve_sends_complete_lines_only_with_mtime_and_full_hb(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    h = hk(-1)
    (src / tm.open_name(h)).write_bytes(b"a\nbb\npart")
    srv, sink = server(src)
    srv.poll(force_hb=True)
    fr = drain(sink)
    d = [f for f in fr if f[0]["k"] == "d"]
    assert len(d) == 1 and d[0][1] == b"a\nbb\n" and d[0][0]["o"] == 0 and d[0][0]["ps"] is None
    assert d[0][0]["mt"] == (src / tm.open_name(h)).stat().st_mtime_ns // 1_000_000
    hb = [f[0] for f in fr if f[0]["k"] == "hb"][-1]
    assert hb["open"][h][:2] == [5, 9] and hb["full"] is True and hb["zq"] == 0
    with open(src / tm.open_name(h), "ab") as fh:
        fh.write(b"ial\n")
    srv.poll(force_hb=True)
    d = [f for f in drain(sink) if f[0]["k"] == "d"]
    assert d[0][1] == b"partial\n" and d[0][0]["o"] == 5 and d[0][0]["ps"] is None  # no stat ever saw the source at exactly 5 bytes
    srv.poll()
    t_clean = srv.open[h]["clean_ms"]
    assert isinstance(t_clean, int)  # this pass saw the source holding exactly what was sent
    with open(src / tm.open_name(h), "ab") as fh:
        fh.write(b"c\n")
    srv.poll()
    d = [f for f in drain(sink) if f[0]["k"] == "d"]
    assert d[0][1] == b"c\n" and d[0][0]["ps"] == t_clean


def test_serve_budget_truncation_reports_not_full_then_catches_up(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    h = hk(-1)
    (src / tm.open_name(h)).write_bytes(b"abc\n" * 10)
    srv, sink = server(src, live_pass_bytes=9)
    srv.poll(force_hb=True)
    fr = drain(sink)
    assert [f[1] for f in fr if f[0]["k"] == "d"] == [b"abc\nabc\n"] and fr[-1][0]["full"] is False
    for _ in range(10):
        srv.poll(force_hb=True)
    fr = drain(sink)
    assert fr[-1][0]["full"] is True and fr[-1][0]["open"][h][0] == 40


def test_serve_refuses_long_line_shrink_and_replace(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    h = hk(-1)
    p = src / tm.open_name(h)
    p.write_bytes(b"x" * 20)
    srv, _ = server(src, live_pass_bytes=8)
    with pytest.raises(tm.Bad, match="line_too_long"):
        srv.poll()
    p.write_bytes(b"a\nb\n")
    srv, _ = server(src)
    srv.poll()
    with open(p, "r+b") as fh:
        fh.truncate(2)
    with pytest.raises(tm.Bad, match="src_shrunk"):
        srv.poll()
    p.write_bytes(b"a\nb\n")
    srv, _ = server(src)
    srv.poll()
    q = src / "other"
    q.write_bytes(b"a\nb\nc\n")
    os.replace(q, p)
    with pytest.raises(tm.Bad, match="src_replaced"):
        srv.poll()


def test_serve_closed_hours_since_and_puller_holdings(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    old, a, b = hk(-20), hk(-3), hk(-2)
    for h in (old, a, b):
        (src / tm.closed_name(h)).write_bytes(zst_bytes(b"r\n" * 1000 + h.encode() + b"\n", tmp_path))
    srv, sink = server(src, since=hk(-10), closed={a}, zst_pass_bytes=100)
    for _ in range(200):
        srv.poll()
    fr = drain(sink)
    ends = [f[0] for f in fr if f[0]["k"] == "ze"]
    assert [e["f"] for e in ends] == [tm.closed_name(b)]  # old < since, a already held
    body = b"".join(f[1] for f in fr if f[0]["k"] == "z" and f[0]["f"] == tm.closed_name(b))
    assert body == (src / tm.closed_name(b)).read_bytes() and ends[0]["sha"] == sha(src / tm.closed_name(b))
    assert max(f[0]["n"] for f in fr if f[0]["k"] == "z") <= 100


def test_serve_follows_compaction_and_reports_gone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    h, g = hk(-2), hk(-3)
    p = src / tm.open_name(h)
    p.write_bytes(b"one\n")
    srv, sink = server(src, have={g: 10})
    srv.poll(force_hb=True)
    fr = drain(sink)
    assert {"k": "gone", "h": g} in [f[0] for f in fr]
    with open(p, "ab") as fh:
        fh.write(b"two\n")
    compact_file(p)  # .zst renamed in, then the .jsonl removed (the feed's own compactor)
    for _ in range(5):
        srv.poll(force_hb=True)
    fr = drain(sink)
    assert [f[0]["k"] for f in fr if f[0]["k"] in ("z", "ze")] == ["z", "ze"]
    assert all(h not in f[0]["open"] for f in fr if f[0]["k"] == "hb")


def test_parse_hello_and_src_refusal(tmp_path: Path) -> None:
    h = hk(-1)
    ok = {"v": 1, "since": hk(-5), "have": {tm.open_name(h): 3}, "closed": [hk(-2)]}
    assert tm.parse_hello(json.dumps(ok).encode()) == (hk(-5), {h: 3}, {hk(-2)})
    bad = [b"nope", json.dumps({**ok, "v": 2}).encode(), json.dumps({**ok, "extra": 1}).encode(), json.dumps({**ok, "since": "2026-10-31"}).encode(),
           json.dumps({**ok, "have": {"../x.jsonl": 1}}).encode(), json.dumps({**ok, "have": {tm.open_name(h): -1}}).encode(),
           json.dumps({**ok, "have": {tm.open_name(h): True}}).encode(), json.dumps({**ok, "closed": [h]}).encode(),
           json.dumps({**ok, "closed": [hk(-9)]}).encode()]
    for raw in bad:
        with pytest.raises(ValueError):
            tm.parse_hello(raw)
    assert tm.src_refusal("/data/mal/meta-capture/creates") == "src_sealed"
    assert tm.src_refusal("/data/mal/blocks/forward-1016/trades") == "src_sealed"
    assert tm.src_refusal(str(tmp_path / "x_shadow")) == "src_sealed"
    assert tm.src_refusal(str(tmp_path / "missing")) == "src_not_dir"
    assert tm.src_refusal(str(tmp_path)) is None


# ---- pull: frame checks --------------------------------------------------------------------------------------------------------
def d_frame(h: str, o: int, body: bytes, mt: int | None = None, ps: int | None = None) -> dict:
    return {"k": "d", "f": tm.open_name(h), "o": o, "n": len(body), "mt": mt, "ps": ps, "ms": 0}


def test_pull_appends_and_refuses_offset_torn_and_closed(mk, tmp_path: Path) -> None:
    pl = mk()
    h = hk(-1)
    pl.handle(d_frame(h, 0, b"a\n"), b"a\n")
    pl.handle(d_frame(h, 2, b"b\n"), b"b\n")
    assert (pl.dir / tm.open_name(h)).read_bytes() == b"a\nb\n"
    with pytest.raises(tm.Mismatch, match="offset"):
        pl.handle(d_frame(h, 2, b"c\n"), b"c\n")
    with pytest.raises(tm.Mismatch, match="torn_frame"):
        pl.handle(d_frame(h, 4, b"c"), b"c")
    with pytest.raises(tm.Mismatch, match="bad_name"):
        pl.handle({**d_frame(h, 4, b"c\n"), "f": "../tape-x.jsonl"}, b"c\n")
    pl.local_closed.add(hk(-2))
    with pytest.raises(tm.Mismatch, match="data_for_closed_hour"):
        pl.handle(d_frame(hk(-2), 0, b"c\n"), b"c\n")
    assert (pl.dir / tm.open_name(h)).read_bytes() == b"a\nb\n"  # nothing unverified written


def test_pull_hb_sync_position_and_source_mtime(mk) -> None:
    pl = mk()
    h = hk(-1)
    pl.handle(d_frame(h, 0, b"a\n"), b"a\n")
    pl.handle({"k": "hb", "ms": 123, "open": {h: [2, 5, 99]}, "zq": 2, "full": True})
    assert pl.synced_ms == 123 and pl.src_mt_ms == 99 and pl.behind_bytes == 3 and pl.zst_pending == 2
    pl.handle({"k": "hb", "ms": 200, "open": {h: [2, 2, 100]}, "zq": 0, "full": False})
    assert pl.synced_ms == 123  # not full: the sync instant does not move
    with pytest.raises(tm.Mismatch, match="hb_position"):
        pl.handle({"k": "hb", "ms": 300, "open": {h: [4, 4, 100]}, "zq": 0, "full": True})
    with pytest.raises(tm.Mismatch, match="bad_hb"):
        pl.handle({"k": "hb", "ms": 300, "open": {h: [2, "x", 100]}, "zq": 0, "full": True})


def test_pull_remote_refusals_reconnect_then_stop(mk) -> None:
    pl = mk()
    for _ in range(tm.REMOTE_BAD_MAX - 1):
        with pytest.raises(tm.Reconnect, match="remote_src_sealed"):
            pl.handle({"k": "bad", "why": "src_sealed"})
    with pytest.raises(tm.Mismatch, match="remote_refusing"):
        pl.handle({"k": "bad", "why": "src_sealed"})


def send_zst(pl: tm.Puller, h: str, data: bytes, chunk: int = 50, sha_hex: str | None = None) -> None:
    for o in range(0, len(data), chunk):
        b = data[o:o + chunk]
        pl.handle({"k": "z", "f": tm.closed_name(h), "o": o, "n": len(b), "size": len(data)}, b)
    pl.handle({"k": "ze", "f": tm.closed_name(h), "size": len(data), "sha": sha_hex or hashlib.sha256(data).hexdigest(), "mt": 1})


def test_pull_installs_closed_hour_after_prefix_check(mk, tmp_path: Path) -> None:
    pl = mk()
    h = hk(-2)
    raw = b"".join(row(1, i) for i in range(50))
    pl.handle(d_frame(h, 0, raw[:raw.index(b"\n") + 1]), raw[:raw.index(b"\n") + 1])  # the live copy holds the first line only
    z = zst_bytes(raw, tmp_path)
    send_zst(pl, h, z)
    assert h in pl.verifying and (pl.dir / (tm.closed_name(h) + tm.PART_SUFFIX)).exists()
    assert pl.drain_verified(block_s=20) == 1
    assert (pl.dir / tm.closed_name(h)).read_bytes() == z and not (pl.dir / tm.open_name(h)).exists() and h in pl.local_closed
    ev = [json.loads(x) for x in pl.events_path.read_text().splitlines()]
    closed = [e for e in ev if e["kind"] == "closed"][0]
    assert closed["zst_sha"] == hashlib.sha256(z).hexdigest() and closed["dec_sha"] == hashlib.sha256(raw).hexdigest()
    assert closed["live_complete"] is False and closed["dec_bytes"] == len(raw)


def test_pull_stops_on_a_diverged_closed_hour(mk, tmp_path: Path) -> None:
    pl = mk()
    h = hk(-2)
    pl.handle(d_frame(h, 0, b"not the same\n"), b"not the same\n")
    send_zst(pl, h, zst_bytes(b"other bytes\n", tmp_path))
    with pytest.raises(tm.Mismatch, match="closed_hour_diverged"):
        pl.drain_verified(block_s=20)
    assert (pl.dir / tm.open_name(h)).read_bytes() == b"not the same\n" and not (pl.dir / tm.closed_name(h)).exists()


def test_pull_zst_sha_mismatch_retries_then_stops(mk, tmp_path: Path) -> None:
    pl = mk()
    h = hk(-2)
    z = zst_bytes(b"x\n" * 100, tmp_path)
    for attempt in range(1, tm.ZST_RETRIES):
        with pytest.raises(tm.Reconnect, match="zst_sha"):
            send_zst(pl, h, z, sha_hex="0" * 64)
        assert not list(pl.dir.glob("*" + tm.PART_SUFFIX)) and pl.zst_retry[h] == attempt
    with pytest.raises(tm.Mismatch, match="zst_retry_exhausted"):
        send_zst(pl, h, z, sha_hex="0" * 64)
    with pytest.raises(tm.Mismatch, match="zst_end_without_part"):
        pl.handle({"k": "ze", "f": tm.closed_name(hk(-3)), "size": 0, "sha": "", "mt": 1})


def test_pull_startup_trims_torn_tail_removes_parts_and_installed_duplicates(tmp_path: Path, mk) -> None:
    dst = tmp_path / "dst"
    dst.mkdir()
    a, b = hk(-1), hk(-2)
    (dst / tm.open_name(a)).write_bytes(b"one\ntwo\ntor")
    (dst / tm.open_name(b)).write_bytes(b"x\n")
    (dst / tm.closed_name(b)).write_bytes(b"zst")  # a crash between the install rename and the unlink
    (dst / (tm.closed_name(hk(-3)) + tm.PART_SUFFIX)).write_bytes(b"half")
    pl = mk(dst)
    assert (dst / tm.open_name(a)).read_bytes() == b"one\ntwo\n" and pl.sizes == {a: 8}
    assert not (dst / tm.open_name(b)).exists() and b in pl.local_closed and not list(dst.glob("*.part"))
    kinds = [json.loads(x)["kind"] for x in pl.events_path.read_text().splitlines()]
    assert "local_torn_trimmed" in kinds and "local_jsonl_removed_after_install" in kinds
    hello = json.loads(pl.hello_line())
    assert hello["have"] == {tm.open_name(a): 8} and hello["closed"] == [b] and hello["since"] == hk(-tm.BACKFILL_HOURS_DEFAULT)


def test_pull_prunes_by_retention_only_tape_names(tmp_path: Path, mk) -> None:
    dst = tmp_path / "dst"
    dst.mkdir()
    old, keep = hk(-30), hk(-5)
    for name in (tm.closed_name(old), tm.open_name(old), tm.closed_name(keep), "notes.txt", "tape-junk.jsonl"):
        (dst / name).write_bytes(b"z\n")
    pl = mk(dst, retain_hours=24, backfill_hours=10)
    left = sorted(p.name for p in dst.iterdir() if p.name != tm.META_DIR)
    assert left == sorted([tm.closed_name(keep), "notes.txt", "tape-junk.jsonl"])
    assert pl._since() == hk(-10)
    with pytest.raises(ValueError):
        mk(tmp_path / "d2", retain_hours=10, backfill_hours=11)


def test_pull_disk_reserve_writes_nothing(mk, tmp_path: Path) -> None:
    DU = namedtuple("DU", "total used free")
    pl = mk(disk_usage=lambda _p: DU(1000, 900, 100), min_free_pct=12.0)
    with pytest.raises(tm.DiskReserve):
        pl.handle(d_frame(hk(-1), 0, b"a\n"), b"a\n")
    assert not (pl.dir / tm.open_name(hk(-1))).exists()


def test_pull_single_writer(mk, tmp_path: Path) -> None:
    mk(tmp_path / "dst")
    with pytest.raises(tm.Locked):
        tm.Puller(tmp_path / "dst", ["true"])


def test_pull_lag_rows_per_minute(mk) -> None:
    pl = mk()
    h = hk(-1)
    now = tm.now_ms()
    pl.handle(d_frame(h, 0, b"a\n", mt=now - 30, ps=now - 50), b"a\n")
    pl.handle(d_frame(h, 2, b"b\n", mt=now - 10, ps=None), b"b\n")
    st = pl.status()["lag_60s"]
    assert st["frames"] == 2 and 10 <= st["new_p50_ms"] <= 10_000 and st["old_max_ms"] >= 50
    pl.flush_minute()
    r = json.loads(pl.lag_path.read_text().splitlines()[-1])
    assert r["frames"] == 2 and r["bytes"] == 4 and r["lag_new_max_ms"] >= 30


def test_remote_serve_cmd_quotes_and_refuses_relative() -> None:
    argv = tm.remote_serve_cmd("mal-research-0", "/data2/mal/wt/tape mirror", "/data/mal/arena/tape")
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and argv[-2] == "mal-research-0"
    assert argv[-1] == "cd '/data2/mal/wt/tape mirror' && exec python3 -u tools/tape_mirror.py serve --src /data/mal/arena/tape"
    for repo, src in (("rel/path", "/x"), ("/a/../b", "/x"), ("/a", "rel")):
        with pytest.raises(ValueError):
            tm.remote_serve_cmd("h", repo, src)


# ---- the reader's verdict ------------------------------------------------------------------------------------------------------
def write_status(dst: Path, **kw) -> None:
    now = tm.now_ms()
    st = {"schema": tm.SCHEMA, "state": "connected", "ms": now, "synced_ms": now - 100, "src_mt_ms": now - 200, "history_hours": 60}
    st.update(kw)
    (dst / tm.META_DIR).mkdir(parents=True, exist_ok=True)
    (dst / tm.META_DIR / "status.json").write_text(json.dumps(st))


def test_mirror_problem_fails_closed(tmp_path: Path) -> None:
    d = tmp_path
    now = tm.now_ms()
    assert tm.mirror_problem(d) == "status_missing"
    write_status(d)
    assert tm.mirror_problem(d) is None and tm.mirror_problem(d, need_hours=60) is None
    assert tm.mirror_problem(d, need_hours=61) == "history_short"
    cases = [({"schema": "x"}, "status_schema"), ({"ms": now + 60_000}, "status_future"), ({"ms": now - 5_000}, "status_stale"),
             ({"state": "stalled"}, "state_stalled"), ({"state": "<b>"}, "state_unspecified"), ({"synced_ms": None}, "never_synced"),
             ({"synced_ms": now - 10_000}, "sync_stale"), ({"src_mt_ms": None}, "source_unknown"), ({"src_mt_ms": now - 60_000}, "source_stale"),
             ({"ms": "soon"}, "status_schema"), ({"synced_ms": True}, "status_schema")]
    for kw, want in cases:
        write_status(d, **kw)
        assert tm.mirror_problem(d) == want, kw
    (d / tm.META_DIR / "status.json").write_text("{not json")
    assert tm.mirror_problem(d) == "status_unreadable"
    (d / tm.META_DIR / "status.json").unlink()
    (d / "elsewhere.json").write_text("{}")
    os.symlink(d / "elsewhere.json", d / tm.META_DIR / "status.json")
    assert tm.mirror_problem(d) == "status_not_regular"


def test_status_cli_exit_codes(tmp_path: Path) -> None:
    write_status(tmp_path)
    r = subprocess.run([sys.executable, str(TOOL), "status", "--dst", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["problem"] is None
    write_status(tmp_path, state="disk_reserve")
    r = subprocess.run([sys.executable, str(TOOL), "status", "--dst", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1 and json.loads(r.stdout)["problem"] == "state_disk_reserve"


# ---- end to end: serve as a subprocess, the puller in a thread ------------------------------------------------------------------
def serve_cmd(src: Path) -> list[str]:
    return [sys.executable, "-u", str(TOOL), "serve", "--src", str(src), "--poll-ms", "5", "--hb-ms", "50"]


def wait_for(pred, timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


class Run:
    def __init__(self, dst: Path, src: Path, **kw) -> None:
        self.pl = tm.Puller(dst, serve_cmd(src), **kw)
        self.stop = threading.Event()
        self.rc: list[int] = []
        self.t = threading.Thread(target=lambda: self.rc.append(self.pl.run(stop=self.stop, max_s=120)), daemon=True)
        self.t.start()

    def end(self) -> int:
        self.stop.set()
        self.t.join(30)
        return self.rc[0]


def same_open(src: Path, dst: Path, h: str) -> bool:
    a, b = src / tm.open_name(h), dst / tm.open_name(h)
    if not b.exists():
        return False
    s = a.read_bytes()
    return b.read_bytes() == s[: s.rfind(b"\n") + 1]


def test_e2e_live_tail_compaction_and_arena_state_reads_the_mirror(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    a, b = hk(-1), hk(0)
    ia = append_rows(src, a, 0, 300)
    ib = append_rows(src, b, 1000, 50)
    run = Run(dst, src)
    try:
        wait_for(lambda: same_open(src, dst, a) and same_open(src, dst, b))
        follower = ArenaState(dst, start_ms=0)  # a reader tailing the MIRROR across the compaction below
        n_follow = follower.poll()
        with open(src / tm.open_name(b), "ab") as fh:  # a partial line is held back until it completes
            line = row(tm.hour_ms(b) + 5000, ib)
            fh.write(line[:30])
            fh.flush()
            time.sleep(0.2)
            assert same_open(src, dst, b)
            fh.write(line[30:])
        ib += 1
        ia = append_rows(src, a, ia, 20)  # a late row for the previous hour, before it closes
        wait_for(lambda: same_open(src, dst, a) and same_open(src, dst, b))
        wait_for(lambda: tm.mirror_problem(dst, src_max_age_ms=600_000) is None)
        n_follow += follower.poll()
        compact_file(src / tm.open_name(a))
        wait_for(lambda: (dst / tm.closed_name(a)).exists() and not (dst / tm.open_name(a)).exists())
        assert sha(dst / tm.closed_name(a)) == sha(src / tm.closed_name(a))
        ib = append_rows(src, b, ib, 30)
        wait_for(lambda: same_open(src, dst, b))
        n_follow += follower.poll()
        wait_for(lambda: json.loads((dst / tm.META_DIR / "status.json").read_text())["closed_verified"] == 1)  # rewritten every 250 ms
        st = json.loads((dst / tm.META_DIR / "status.json").read_text())
        assert st["closed_verified"] == 1 and st["history_hours"] >= 2 and st["lag_60s"]["frames"] > 0
    finally:
        assert run.end() == 0
    fresh_src, fresh_dst = ArenaState(src, start_ms=0), ArenaState(dst, start_ms=0)
    total = fresh_src.poll()
    assert total == ia + (ib - 1000) == fresh_dst.poll() == n_follow
    assert fresh_dst.pool(MINT).n_trades == fresh_src.pool(MINT).n_trades == total
    ev = [json.loads(x) for x in (dst / tm.META_DIR / "events.jsonl").read_text().splitlines()]
    closed = [e for e in ev if e["kind"] == "closed"]
    assert len(closed) == 1 and closed[0]["live_complete"] is True
    assert json.loads((dst / tm.META_DIR / "status.json").read_text())["state"] == "stopped"


def test_e2e_restart_resumes_without_duplicates_and_backfills(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    old, a, b = hk(-5), hk(-1), hk(0)
    (src / tm.closed_name(old)).write_bytes(zst_bytes(b"".join(row(1, i) for i in range(400)), tmp_path))
    append_rows(src, a, 0, 100)
    append_rows(src, b, 500, 10)
    run = Run(dst, src)
    try:
        wait_for(lambda: same_open(src, dst, a) and same_open(src, dst, b) and (dst / tm.closed_name(old)).exists())
    finally:
        assert run.end() == 0
    with open(dst / tm.open_name(b), "ab") as fh:
        fh.write(b'{"torn')  # our own crash mid-write
    append_rows(src, a, 100, 50)
    compact_file(src / tm.open_name(a))  # the hour closes while the puller is down: the local copy is a strict prefix
    append_rows(src, b, 510, 10)
    run = Run(dst, src)
    try:
        wait_for(lambda: (dst / tm.closed_name(a)).exists() and same_open(src, dst, b))
    finally:
        assert run.end() == 0
    for h in (old, a):
        assert sha(dst / tm.closed_name(h)) == sha(src / tm.closed_name(h))
    assert not (dst / tm.open_name(a)).exists()
    ev = [json.loads(x) for x in (dst / tm.META_DIR / "events.jsonl").read_text().splitlines()]
    closed = {e["h"]: e for e in ev if e["kind"] == "closed"}
    assert closed[a]["live_complete"] is False and closed[a]["live_bytes"] < closed[a]["dec_bytes"]
    assert sum(e["kind"] == "closed" and e["h"] == old for e in ev) == 1  # never transferred twice
    assert any(e["kind"] == "local_torn_trimmed" for e in ev)


def test_e2e_killed_session_reconnects_and_stays_exact(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    h = hk(0)
    i = append_rows(src, h, 0, 50)
    run = Run(dst, src)
    try:
        wait_for(lambda: same_open(src, dst, h))
        first = run.pl.proc
        os.killpg(first.pid, 9)
        i = append_rows(src, h, i, 50)
        wait_for(lambda: run.pl.proc is not first and run.pl.state == "connected" and same_open(src, dst, h))
        i = append_rows(src, h, i, 50)
        wait_for(lambda: same_open(src, dst, h))
    finally:
        assert run.end() == 0
    kinds = [json.loads(x)["kind"] for x in (dst / tm.META_DIR / "events.jsonl").read_text().splitlines()]
    assert "disconnect" in kinds and "reconnect" in kinds


def test_e2e_serve_refusal_reaches_the_puller(tmp_path: Path) -> None:
    dst = tmp_path / "dst"
    pl = tm.Puller(dst, [sys.executable, "-u", str(TOOL), "serve", "--src", str(tmp_path / "missing")])
    stop = threading.Event()
    t = threading.Thread(target=lambda: pl.run(stop=stop, max_s=20), daemon=True)
    t.start()
    wait_for(lambda: (dst / tm.META_DIR / "events.jsonl").exists() and "remote_src_not_dir" in (dst / tm.META_DIR / "events.jsonl").read_text())
    stop.set()
    t.join(30)
    assert tm.mirror_problem(dst) is not None


# ---- the MiScusi job script (DRY and refusals only: no ssh, no mkdir) -------------------------------------------------------------
JOB = REPO / "scripts" / "ops" / "tape-mirror-pull-job.sh"


def job(env_extra: dict, home: Path) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
    env.update(env_extra)
    return subprocess.run(["sh", str(JOB)], env=env, capture_output=True, text=True, timeout=60)


def test_job_script_is_posix_sh_and_executable() -> None:
    assert os.access(JOB, os.X_OK) and JOB.read_text().splitlines()[0] == "#!/bin/sh"
    assert subprocess.run(["sh", "-n", str(JOB)]).returncode == 0
    if shutil.which("dash"):
        assert subprocess.run(["dash", "-n", str(JOB)]).returncode == 0
    text = "\n".join(ln for ln in JOB.read_text().splitlines() if not ln.lstrip().startswith("#"))
    for bad in ("[[", "]]", "<<<", "$'", "PIPESTATUS", "function ", "local ", "echo -e", "source ", "declare ", "${!", "=~", "&>", "<("):
        assert bad not in text, bad


def test_job_script_dry_line(tmp_path: Path) -> None:
    r = job({"DRY": "1", "REMOTE_REPO": "/data2/mal/wt/tape-mirror-serve"}, tmp_path)
    assert r.returncode == 0, r.stdout
    want = (f"python3 -u tools/tape_mirror.py pull --ssh-host mal-research-0 --remote-repo /data2/mal/wt/tape-mirror-serve --src /data/mal/arena/tape "
            f"--dst {tmp_path}/mal-data/arena-mirror/tape --retain-hours 72 --backfill-hours 64 --min-free-pct 12")
    assert r.stdout.strip().endswith("then run: " + want)
    assert not (tmp_path / "mal-data").exists()


@pytest.mark.parametrize("env, why", [
    ({}, "REMOTE_REPO is required"),
    ({"REMOTE_REPO": "rel/repo"}, "REMOTE_REPO must be"),
    ({"REMOTE_REPO": "/a/../b"}, "REMOTE_REPO must be"),
    ({"REMOTE_REPO": "/a;rm -rf x"}, "REMOTE_REPO must be"),
    ({"SRC": "/data/mal/arena/tape x"}, "SRC must be"),
    ({"DST": "/tmp/elsewhere"}, "DST must be under"),
    ({"SSH_HOST": "host -oProxyCommand=x"}, "SSH_HOST must be"),
    ({"BACKFILL_H": "80"}, "BACKFILL_H must be 1..RETAIN_H"),
    ({"BACKFILL_H": "0"}, "BACKFILL_H must be 1..RETAIN_H"),
    ({"RETAIN_H": "400", "BACKFILL_H": "10"}, "RETAIN_H must be at most 336"),
    ({"MIN_FREE_PCT": "0"}, "MIN_FREE_PCT must be 1..50"),
    ({"MIN_FREE_PCT": "12.5"}, "must be non-negative integers"),
])
def test_job_script_refusals(tmp_path: Path, env: dict, why: str) -> None:
    base = {"DRY": "1", "REMOTE_REPO": "/data2/mal/wt/tape-mirror-serve"}
    if "REMOTE_REPO" not in env and not env:
        base.pop("REMOTE_REPO")
    r = job({**base, **env}, tmp_path)
    assert r.returncode == 2 and why in r.stdout, r.stdout


@pytest.mark.skipif(os.uname().nodename == "mal-fast-0", reason="on mal-fast-0 a live-form run would go on to ssh")
def test_job_script_runs_on_fast0_only(tmp_path: Path) -> None:
    r = job({"REMOTE_REPO": "/data2/mal/wt/tape-mirror-serve"}, tmp_path)
    assert r.returncode == 2 and "runs on mal-fast-0 only" in r.stdout and not (tmp_path / "mal-data").exists()


def test_pull_cli_writes_the_miscusi_result_and_refuses_two_sources(tmp_path: Path, monkeypatch) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    append_rows(src, hk(0), 0, 20)
    res = tmp_path / "result.json"
    monkeypatch.setenv("MISCUSI_RESULT", str(res))
    monkeypatch.delenv("MISCUSI_PROGRESS", raising=False)
    assert tm.main(["pull", "--dst", str(dst), "--cmd-json", json.dumps(serve_cmd(src)), "--max-s", "1.5"]) == 0
    r = json.loads(res.read_text())
    assert r["ok"] is True and r["metrics"]["state"] == "stopped" and r["metrics"]["live_bytes"] == (src / tm.open_name(hk(0))).stat().st_size
    assert tm.main(["pull", "--dst", str(dst), "--cmd-json", "[]", "--ssh-host", "h"]) == tm.EXIT_REFUSED
