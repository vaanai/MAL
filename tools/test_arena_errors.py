"""Review item 2: a decode or write error is never silent. It becomes a data_loss gap row, or the process exits non-zero. No network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from observe.fake_ws import fixture_line
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.test_arena_feed import roomy

T = 1_790_316_000_000


def notice(i: int, t: int | None = None, slot: int | None = None) -> RawNotice:
    return RawNotice(slot=slot if slot is not None else 100 + i, signature=f"{i:088d}", failed=False, logs=(fixture_line("pumpswap_buy_event.b64"),),
                     t_recv_ms=t if t is not None else T + i * 100, commitment="confirmed", feed="f")


def feed_on(tmp_path: Path, clock: dict | None = None) -> af.Feed:
    clock = clock if clock is not None else {"t": T}
    return af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, disk_usage=roomy, compress=False)


def tape_rows(d: Path) -> list[dict]:
    return [json.loads(ln) for f in sorted(d.glob("tape-*.jsonl")) for ln in f.read_text().splitlines()]


def test_decode_errors_become_one_coalesced_data_loss_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = feed_on(tmp_path)

    def boom(note, cache, counters=None):
        raise ValueError("bad blob")

    monkeypatch.setattr(af, "rows_from_notice", boom)
    assert feed.handle_notice("pumpswap", notice(1)) == 0 and feed.handle_notice("pumpswap", notice(2)) == 0
    assert feed.counters["decode_errors"] == 2 and feed.flush_pending(T + 150) == 0  # a burst is coalesced until it has been quiet for LOSS_FLUSH_MS
    assert feed.flush_pending(T + 200 + af.LOSS_FLUSH_MS) == 1
    feed.writer.close()
    (gap,) = [r for r in tape_rows(tmp_path) if r["type"] == "gap"]
    assert (gap["kind"], gap["prog"], gap["data_loss"], gap["count"], gap["from_ms"], gap["to_ms"], gap["from_slot"], gap["to_slot"]) == (
        "decode_error", "pumpswap", True, 2, T + 100, T + 200, 101, 102)
    assert not feed.pending


def test_a_failing_writer_does_not_end_the_consumer_and_the_loss_is_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": T}
    feed = feed_on(tmp_path, clock)
    real, calls = feed.writer.write, {"n": 0}

    def flaky(rec, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("EIO")
        return real(rec, **kw)

    monkeypatch.setattr(feed.writer, "write", flaky)

    async def go() -> None:
        for i in (1, 2, 3):
            await feed.queue.put(("pumpswap", notice(i)))
        task = asyncio.create_task(feed.consume())
        while not feed.queue.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert not task.done()  # the consumer is still alive after two failed writes
        feed.stop.set()
        await task

    asyncio.run(go())
    assert feed.counters["write_errors"] == 2 and feed.write_fail_since_ms is None  # the third write succeeded
    assert list(feed.pending) == [("pumpswap", "write_error")] and feed.pending[("pumpswap", "write_error")]["count"] == 2
    feed.stop.set()
    assert feed.flush_pending(T + 10_000) == 1
    feed.writer.close()
    rows = tape_rows(tmp_path)
    assert [r["type"] for r in rows] == ["trade", "gap"]
    assert rows[1]["kind"] == "write_error" and rows[1]["data_loss"] is True and rows[1]["count"] == 2


def test_an_unexpected_exception_in_one_notice_is_a_consume_error_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = feed_on(tmp_path)
    real, seen = feed.handle_notice, []

    def sometimes(prog, note):
        seen.append(note.signature)
        if len(seen) == 1:
            raise RuntimeError("bug")
        return real(prog, note)

    monkeypatch.setattr(feed, "handle_notice", sometimes)

    async def go() -> None:
        for i in (1, 2):
            await feed.queue.put(("pumpswap", notice(i)))
        task = asyncio.create_task(feed.consume())
        while not feed.queue.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        feed.stop.set()
        await task

    asyncio.run(go())
    assert len(seen) == 2 and feed.counters["consume_errors"] == 1 and feed.pending[("pumpswap", "consume_error")]["count"] == 1
    assert feed.writer.lines == 1  # the second notice was processed


def test_a_loss_waits_while_the_writer_is_paused_below_the_reserve(tmp_path: Path) -> None:
    feed = feed_on(tmp_path)
    feed.note_loss("pumpfun", "decode_error", T, 5)
    feed.writer.paused = True
    feed.stop.set()
    assert feed.flush_pending(T + 10_000) == 0 and ("pumpfun", "decode_error") in feed.pending
    feed.writer.paused = False
    assert feed.flush_pending(T + 10_001) == 1


def test_writes_failing_for_two_minutes_end_the_process_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": T}
    feed = feed_on(tmp_path, clock)
    monkeypatch.setattr(feed.writer, "write", lambda rec, **kw: (_ for _ in ()).throw(OSError("EIO")))
    assert feed.emit_safe({"type": "trade", "prog": "pumpfun", "slot": 1, "t_recv_ms": T, "signature": "s", "event_index": 0}) is False
    assert feed.write_fail_since_ms == T and feed.fatal is None
    clock["t"] = T + af.WRITE_FAIL_EXIT_MS - 1
    feed.tick(clock["t"])
    assert feed.fatal is None and not feed.stop.is_set()
    clock["t"] = T + af.WRITE_FAIL_EXIT_MS
    feed.tick(clock["t"])
    assert feed.fatal == "write_failing" and feed.stop.is_set()


def test_run_live_returns_the_fatal_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(self, max_seconds=None):
        self.fatal = "write_failing"

    monkeypatch.setattr(af.Feed, "run", fake_run)
    args = af.build_parser().parse_args(["--out-dir", str(tmp_path), "--no-resolve", "--no-compress", "--min-free-pct", "0"])
    assert asyncio.run(af.run_live(args)) == af.FATAL_EXIT == 4


def test_a_core_task_that_ends_early_is_fatal(tmp_path: Path) -> None:
    feed = feed_on(tmp_path)

    async def go() -> None:
        async def dies():
            return None

        t = asyncio.create_task(dies())
        t.add_done_callback(feed._core_task_ended)
        await t
        await asyncio.sleep(0)

    asyncio.run(go())
    assert feed.fatal == "core_task_ended" and feed.stop.is_set()
