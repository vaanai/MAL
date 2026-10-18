"""DEC-031 rev 2 s3.8 "Walk 2 keeps its room": no arena write takes the filesystem's free space below 25%. No network."""

from __future__ import annotations

import collections
import json
import shutil
from pathlib import Path

import pytest

from tools import arena_feed as af
from tools import arena_zst as az
from tools.test_arena_feed import DU
from tools.test_arena_state import H, curve, hour_name

H0, H1 = 1_790_316_000_000, 1_790_316_000_000 + H  # two consecutive UTC hours


class Disk:
    """A fake shutil.disk_usage: free space settable between calls, calls counted."""

    def __init__(self, free_pct: float, total: int = 10**12) -> None:
        self.total, self.free, self.calls = total, int(free_pct * total / 100), 0

    def set_pct(self, free_pct: float) -> None:
        self.free = int(free_pct * self.total / 100)

    def __call__(self, _path) -> DU:
        self.calls += 1
        return DU(self.total, self.total - self.free, self.free)


def feed_on(tmp_path: Path, disk, clock: dict, **kw) -> af.Feed:
    return af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, disk_usage=disk, compress=kw.pop("compress", False), **kw)


def row(t: int, sig: str = "s") -> dict:
    return {"v": 1, "type": "trade", "prog": "pumpfun", "slot": 1, "t_recv_ms": t, "signature": sig, "event_index": 0}


def tape_files(d: Path) -> list[Path]:
    return sorted(d.glob("tape-*.jsonl"))


def test_default_is_25_and_free_is_measured_like_the_walker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert af.MIN_FREE_PCT_DEFAULT == 25.0 and af.build_parser().parse_args([]).min_free_pct == 25.0 and af.DISK_CHECK_S == 60.0
    clock = {"t": H0}
    real = shutil.disk_usage(tmp_path)
    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, compress=False)  # the default measure
    assert feed.min_free_pct == 25.0
    assert feed.measure_free_pct() == pytest.approx(100.0 * real.free / real.total, rel=1e-3)  # shutil.disk_usage(path).free / total
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(1000, 750, 250))  # exactly 25.0%: allowed
    assert feed.measure_free_pct() == 25.0 and feed.check_disk(H0, force=True) is True and not feed.writer.paused
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(1000, 751, 249))  # 24.9%: not allowed
    assert feed.check_disk(H0, force=True) is False and feed.writer.paused


def test_below_the_reserve_nothing_is_written_then_one_gap_record(tmp_path: Path) -> None:
    disk, clock = Disk(24.0), {"t": H0 + 1000}
    feed = feed_on(tmp_path, disk, clock)
    feed.emit(feed.meta_row("start", schema=af.SCHEMA))  # the first write checks before it opens the hour file
    for i in range(5):
        feed.emit(row(H0 + 2000 + i, f"a{i}"))
    feed.heartbeat(clock["t"])  # two hb rows are dropped; the status file is the only thing written, and it carries the flag
    assert tape_files(tmp_path) == [] and feed.writer.lines == 0 and feed.writer.paused
    st = json.loads((tmp_path / af.STATUS_NAME).read_text())
    assert st["writing_paused"] is True and st["disk_reserve"]["paused"] is True and st["disk_reserve"]["min_free_pct"] == 25.0
    assert st["disk_reserve"]["free_pct"] == 24.0 and st["disk_reserve"]["pauses"] == 1 and st["rows_dropped_disk"] == 1 + 5 + 2
    # space comes back: ONE gap record, then rows flow again
    disk.set_pct(30.0)
    clock["t"] = H0 + 90_000
    assert feed.check_disk(clock["t"], force=True) is True
    feed.emit(row(H0 + 91_000, "b"))
    feed.writer.close()
    lines = [json.loads(ln) for f in tape_files(tmp_path) for ln in f.read_text().splitlines()]
    assert [r["type"] for r in lines] == ["gap", "trade"]
    gap = lines[0]
    assert gap["kind"] == "disk_reserve" and gap["reason"] == "disk_reserve" and gap["data_loss"] is True and gap["rows_dropped"] == 8
    assert gap["paused_since_ms"] == H0 + 1000 and gap["paused_ms"] == 89_000 and gap["free_pct"] == 30.0 and gap["min_free_pct"] == 25.0
    json.dumps(gap, allow_nan=False)


def test_the_disk_is_checked_before_each_hour_file_opens(tmp_path: Path) -> None:
    disk, clock = Disk(40.0), {"t": H0 + 1000}
    feed = feed_on(tmp_path, disk, clock)
    feed.emit(row(H0 + 1000, "x"))
    assert [p.name for p in tape_files(tmp_path)] == [hour_name(H0)] and disk.calls == 1
    disk.set_pct(10.0)  # drops below the reserve; the 60 s check has not come round yet
    feed.emit(row(H0 + 2000, "y"))  # same hour, file already open: the periodic check is what covers this
    assert feed.writer.lines == 2 and not feed.writer.paused
    clock["t"] = H1 + 5
    feed.emit(row(H1 + 5, "z"))  # a new hour file would open: the check runs first and refuses
    assert [p.name for p in tape_files(tmp_path)] == [hour_name(H0)] and feed.writer.paused and disk.calls == 2
    assert feed.writer.dropped == 1


def test_the_disk_is_checked_at_least_every_60_seconds(tmp_path: Path) -> None:
    disk, clock = Disk(50.0), {"t": H0}
    feed = feed_on(tmp_path, disk, clock)
    assert feed.check_disk(H0) is True and disk.calls == 1
    assert feed.check_disk(H0 + 1000) is True and disk.calls == 1  # inside the interval: not measured again
    disk.set_pct(5.0)
    assert feed.check_disk(H0 + 2000) is True and disk.calls == 1  # still inside it
    feed.last_disk -= af.DISK_CHECK_S + 1  # 61 s later
    assert feed.check_disk(H0 + 62_000) is False and disk.calls == 2 and feed.writer.paused
    feed.emit(row(H0 + 63_000))
    assert tape_files(tmp_path) == []


def test_compaction_is_an_arena_write_too(tmp_path: Path) -> None:
    if az.zstd_path() is None:
        pytest.skip("zstd CLI not installed")
    src = tmp_path / hour_name(H0)
    src.write_text("".join(json.dumps(curve(H0 + i, "w", "buy", 1.0, 1e-7)) + "\n" for i in range(300)))
    size = src.stat().st_size
    total = 10**6
    disk, clock = Disk(25.0, total=total), {"t": H1 + 400_000}  # exactly at the reserve: the .zst (estimated at half the source) would cross it
    feed = feed_on(tmp_path, disk, clock, compress=True)
    feed.compactor.start()
    try:
        feed.compactor.scan(clock["t"], None, force=True)
        feed.compactor.drain()
        assert src.exists() and not (tmp_path / (src.name + ".zst")).exists() and feed.compactor.snapshot()["skipped_disk"] == 1
        disk.set_pct(60.0)  # room again: the next scan compacts
        feed.compactor.scan(clock["t"], None, force=True)
        feed.compactor.drain()
    finally:
        feed.compactor.stop()
    assert not src.exists() and (tmp_path / (src.name + ".zst")).exists() and feed.compactor.snapshot()["files"] == 1
    assert feed.compactor.snapshot()["failed"] == 0


def gaps_on_tape(d: Path) -> list[dict]:
    return [r for f in tape_files(d) for r in map(json.loads, f.read_text().splitlines()) if r["type"] == "gap"]


def test_free_space_hovering_at_the_reserve_keeps_every_pause_record(tmp_path: Path) -> None:
    """Review item 3: 24 -> 30 -> 24 -> 30. Each pause gets its own correct gap record; none is lost or has a null span."""
    disk, clock = Disk(24.0), {"t": H0 + 1000}
    feed = feed_on(tmp_path, disk, clock)
    feed.emit(row(H0 + 1000, "a"))  # pause 1 (the pre-open check)
    disk.set_pct(30.0)
    clock["t"] = H0 + 11_000
    assert feed.check_disk(clock["t"], force=True) is True and not feed.writer.paused
    feed.emit(row(H0 + 12_000, "b"))
    disk.set_pct(24.0)
    clock["t"] = H0 + 20_000
    assert feed.check_disk(clock["t"], force=True) is False and feed.writer.paused  # pause 2
    feed.emit(row(H0 + 21_000, "c"))
    feed.emit(row(H0 + 22_000, "d"))
    disk.set_pct(30.0)
    clock["t"] = H0 + 30_000
    assert feed.check_disk(clock["t"], force=True) is True
    feed.writer.close()
    g = gaps_on_tape(tmp_path)
    assert [(x["paused_since_ms"], x["paused_ms"], x["rows_dropped"], x["from_ms"], x["to_ms"]) for x in g] == [
        (H0 + 1000, 10_000, 1, H0 + 1000, H0 + 11_000), (H0 + 20_000, 10_000, 2, H0 + 20_000, H0 + 30_000)]
    assert feed.pauses == 2 and feed.paused_since_ms is None


def test_resume_needs_the_reserve_plus_the_margin(tmp_path: Path) -> None:
    disk, clock = Disk(24.0), {"t": H0}
    feed = feed_on(tmp_path, disk, clock)
    assert feed.check_disk(H0, force=True) is False
    for pct in (25.0, 26.0, 26.9):  # at and just above the reserve: still paused, nothing written
        disk.set_pct(pct)
        assert feed.check_disk(H0 + 1, force=True) is False and feed.writer.paused
    assert tape_files(tmp_path) == []
    disk.set_pct(25.0 + af.RESUME_MARGIN_PCT)
    assert feed.check_disk(H0 + 2, force=True) is True and not feed.writer.paused
    assert len(gaps_on_tape(tmp_path)) == 1


def test_a_failed_resume_write_keeps_the_pause_and_the_span(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    disk, clock = Disk(24.0), {"t": H0 + 1000}
    feed = feed_on(tmp_path, disk, clock)
    feed.emit(row(H0 + 1000))
    disk.set_pct(40.0)
    real = feed.writer.write
    calls = {"n": 0}

    def flaky(rec, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real(rec, **kw)

    monkeypatch.setattr(feed.writer, "write", flaky)
    clock["t"] = H0 + 9000
    assert feed.check_disk(clock["t"], force=True) is False and feed.writer.paused and feed.paused_since_ms == H0 + 1000
    clock["t"] = H0 + 19_000
    assert feed.check_disk(clock["t"], force=True) is True
    g = gaps_on_tape(tmp_path)
    assert len(g) == 1 and g[0]["paused_since_ms"] == H0 + 1000 and g[0]["to_ms"] == H0 + 19_000 and feed.counters["write_errors"] == 1


def test_the_error_log_is_an_arena_write_too(tmp_path: Path) -> None:
    disk, clock = Disk(24.0), {"t": H0}
    feed = feed_on(tmp_path, disk, clock)
    feed.check_disk(H0, force=True)
    feed.errlog.log(RuntimeError("x"), {"where": "test"})
    assert not (tmp_path / af.ERRORS_NAME).exists() and feed.errlog.dropped == 1
    disk.set_pct(40.0)
    feed.check_disk(H0 + 1, force=True)
    feed.errlog.log(RuntimeError("y"), {"where": "test"})
    assert (tmp_path / af.ERRORS_NAME).exists()
