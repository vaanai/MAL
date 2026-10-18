"""Hour compaction of the arena tape (.jsonl -> .jsonl.zst) and the reader that follows both. Needs the zstd CLI. No network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools import arena_feed as af
from tools import arena_zst as az
from tools.arena_state import ArenaState
from tools.test_arena_feed import roomy
from tools.test_arena_state import H, M, MIN, T0, curve, hour_name, write_tape

pytestmark = pytest.mark.skipif(az.zstd_path() is None, reason="zstd CLI not installed")

H0, H1, H2 = T0, T0 + H, T0 + 2 * H  # three consecutive UTC hours (T0 is an hour boundary)


def jsonl(tmp_path: Path, t: int, n: int = 200) -> Path:
    p = tmp_path / hour_name(t)
    p.write_text("".join(json.dumps(curve(t + i * 1000, f"w{i % 7}", "buy", 1.0, 1e-7)) + "\n" for i in range(n)))
    return p


def zst_copy(src: Path) -> Path:
    dst = src.with_name(src.name + ".zst")
    with open(dst, "wb") as out:
        subprocess.run(["zstd", "-q", "-c", str(src)], stdout=out, check=True)
    return dst


# ---- compact_file ----------------------------------------------------------------------------------------------------
def test_compact_roundtrip_verifies_and_deletes_the_source(tmp_path: Path) -> None:
    src = jsonl(tmp_path, H0)
    data = src.read_bytes()
    info = az.compact_file(src)
    dst = tmp_path / (src.name + ".zst")
    assert not src.exists() and dst.exists() and not list(tmp_path.glob("*.tmp"))
    assert info["bytes_in"] == len(data) and info["lines"] == 200 and info["bytes_out"] == dst.stat().st_size and info["ratio"] > 1.5
    r = az.open_zst(dst)
    try:
        assert az.scan_stream(r.read)[0] == len(data)
    finally:
        r.close()


def test_verify_mismatch_removes_the_zst_and_keeps_the_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = jsonl(tmp_path, H0)

    class Bad:
        def __init__(self, path) -> None:
            self.sent = False

        def read(self, n=0) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return b'{"x":1}\n'

        def close(self) -> None:
            pass

    monkeypatch.setattr(az, "open_zst", Bad)
    with pytest.raises(az.CompactError):
        az.compact_file(src)
    assert src.exists() and not (tmp_path / (src.name + ".zst")).exists()


def test_truncated_zst_raises_on_close(tmp_path: Path) -> None:
    z = zst_copy(jsonl(tmp_path, H0, n=2000))
    z.write_bytes(z.read_bytes()[:-20])
    r = az.open_zst(z)
    while r.read(1 << 16):
        pass
    with pytest.raises(az.CompactError):
        r.close()


# ---- Compactor / writer / feed -----------------------------------------------------------------------------------------
def test_compactor_picks_only_closed_hours_and_cleans_tmp(tmp_path: Path) -> None:
    old, closing, cur = jsonl(tmp_path, H0), jsonl(tmp_path, H1), jsonl(tmp_path, H2)
    (tmp_path / (hour_name(H0) + ".zst.tmp")).write_bytes(b"junk")
    now = H2 + 60_000  # H1 ended 60 s ago: inside the 120 s grace; H0 is closed; H2 is the writer's file
    c = af.Compactor(tmp_path, wall=lambda: now)
    c.start()
    try:
        assert c.scan(now, cur.name, force=True) == 1
        c.drain()
    finally:
        c.stop()
    assert not old.exists() and (tmp_path / (old.name + ".zst")).exists() and closing.exists() and cur.exists()
    assert not list(tmp_path.glob("*.tmp")) and c.snapshot()["files"] == 1 and c.snapshot()["failed"] == 0
    assert c.scan(H2 + 130_000, cur.name, force=True) == 1  # the grace has passed for H1 now


def test_compactor_failure_keeps_source_and_gives_up_after_three_scans(tmp_path: Path) -> None:
    src = jsonl(tmp_path, H0)

    def boom(p: Path) -> dict:
        raise az.CompactError("no")

    c = af.Compactor(tmp_path, wall=lambda: H2, compact=boom)
    c.start()
    try:
        for _ in range(5):
            c.scan(H2, None, force=True)
            c.drain()
    finally:
        c.stop()
    assert src.exists() and c.snapshot()["failed"] == af.COMPACT_TRIES and c.snapshot()["last_error"] == "CompactError"


def test_writer_sends_a_late_row_of_a_compacted_hour_to_the_current_file(tmp_path: Path) -> None:
    w = af.TapeWriter(tmp_path)
    row = lambda t: {"v": 1, "type": "hb", "prog": "all", "slot": 0, "t_recv_ms": t, "signature": None, "event_index": None}  # noqa: E731
    w.write(row(H0 + 5))
    w.write(row(H1 + 5))
    az.compact_file(tmp_path / hour_name(H0))
    w.write(row(H0 + 9))  # arrives after its hour was compacted
    w.close()
    assert not (tmp_path / hour_name(H0)).exists() and w.late_to_current == 1
    assert len((tmp_path / hour_name(H1)).read_text().splitlines()) == 2


def test_release_stale_closes_the_old_handle(tmp_path: Path) -> None:
    w = af.TapeWriter(tmp_path)
    w.write({"v": 1, "type": "hb", "prog": "all", "slot": 0, "t_recv_ms": H0 + 5, "signature": None, "event_index": None})
    w.release_stale(H0 + 3_600_000 + 100_000)
    assert w.current_name == hour_name(H0)  # inside the grace
    w.release_stale(H0 + 3_600_000 + af.COMPACT_GRACE_MS)
    assert w.current_name is None


def test_feed_needs_zstd_unless_told_not_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(af, "zstd_path", lambda: None)
    with pytest.raises(ValueError):
        af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, rpc_url=None)
    af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, rpc_url=None, compress=False)


def test_feed_tick_compacts_a_closed_hour_in_the_background(tmp_path: Path) -> None:
    clock = {"t": H0 + 10_000}
    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, disk_usage=roomy)
    feed.compactor.start()
    try:
        for i in range(50):
            feed.emit({"v": 1, "type": "trade", "prog": "pumpfun", "slot": i, "t_recv_ms": H0 + 20_000 + i, "signature": f"s{i}", "event_index": 0})
        clock["t"] = H1 + 200_000
        feed.emit({"v": 1, "type": "trade", "prog": "pumpfun", "slot": 99, "t_recv_ms": clock["t"], "signature": "s99", "event_index": 0})
        feed.tick(clock["t"])
        feed.compactor.drain()
        st = feed.status(clock["t"])
    finally:
        feed.compactor.stop()
        feed.writer.close()
    assert (tmp_path / (hour_name(H0) + ".zst")).exists() and not (tmp_path / hour_name(H0)).exists() and (tmp_path / hour_name(H1)).exists()
    assert st["compaction"]["files"] == 1 and st["compaction"]["enabled"] is True and st["compaction"]["bytes_out"] < st["compaction"]["bytes_in"]
    assert feed.min_free_pct == 25.0 and af.build_parser().parse_args([]).min_free_pct == 25.0


# ---- the reader follows .jsonl and .jsonl.zst --------------------------------------------------------------------------
def test_state_reads_closed_hours_from_zst_and_the_current_hour_from_jsonl(tmp_path: Path) -> None:
    jsonl(tmp_path, H0, 120)
    jsonl(tmp_path, H1, 80)
    az.compact_file(tmp_path / hour_name(H0))
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 200 and st.pool(M).n_trades == 200 and st.counters["bad_lines"] == 0 and st.counters["zst_errors"] == 0
    assert st.poll() == 0
    only_h1 = ArenaState(tmp_path, start_ms=H1)  # the compacted hour before start_ms is not even opened
    assert only_h1.poll() == 80


def test_tail_continues_at_the_same_byte_after_the_hour_is_compacted(tmp_path: Path) -> None:
    src = jsonl(tmp_path, H0, 100)
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 100
    with src.open("a") as fh:  # rows the reader has not taken yet
        for i in range(100, 130):
            fh.write(json.dumps(curve(H0 + i * 1000, "late", "buy", 1.0, 1e-7)) + "\n")
    az.compact_file(src)
    assert st.poll() == 30 and st.pool(M).n_trades == 130  # exactly once: skipped the 100 it had, took the 30 it had not
    write_tape(tmp_path, [curve(H1 + 5, "next", "buy", 1.0, 1e-7)])
    assert st.poll() == 1 and st.pool(M).n_trades == 131 and st.poll() == 0


def test_both_files_present_during_verification_are_read_once(tmp_path: Path) -> None:
    src = jsonl(tmp_path, H0, 60)
    zst_copy(src)
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 60 and st.pool(M).n_trades == 60


def test_corrupt_zst_is_counted_not_raised(tmp_path: Path) -> None:
    (tmp_path / (hour_name(H0) + ".zst")).write_bytes(b"this is not zstd")
    write_tape(tmp_path, [curve(H1 + 5, "x", "buy", 1.0, 1e-7)])
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 1 and st.counters["zst_errors"] == 1
