"""Quant-proof requirement from #708: a gap row is written BEFORE the first trade row after it. The engine assumes that order. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from observe.fake_ws import fixture_line
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.test_arena_feed import roomy

T = 1_790_316_000_000


def notice(slot: int, t: int) -> RawNotice:
    return RawNotice(slot=slot, signature=f"{slot:088d}", failed=False, logs=(fixture_line("pumpswap_buy_event.b64"),), t_recv_ms=t, commitment="c", feed="f")


def make_feed(tmp_path: Path, clock: dict) -> af.Feed:
    return af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, disk_usage=roomy, compress=False, stagger_s=0.0)


def tape(d: Path) -> list[dict]:
    return [json.loads(ln) for f in sorted(d.glob("tape-*.jsonl")) for ln in f.read_text().splitlines()]


def shape(rows: list[dict]) -> list[str]:
    """The pumpswap trade and gap rows, in file order (the idle pumpfun watch logs its own silence in some tests)."""
    return [r["kind"] + ("/closed" if r.get("closed") else "/open" if r.get("closed") is False else "") if r["type"] == "gap" else r["type"]
            for r in rows if r["type"] in ("trade", "gap") and r["prog"] == "pumpswap"]


def assert_gap_first(rows: list[dict]) -> None:
    """The engine's assumption, as a property of the file: no trade row that sits BEFORE a gap row has t_recv_ms at or after the gap's to_ms.
    to_ms is the t_recv_ms of the first notice after the gap, so that notice's rows must come after the gap row."""
    for i, g in enumerate(rows):
        if g["type"] != "gap":
            continue
        early = [r for r in rows[:i] if r["type"] == "trade" and r["t_recv_ms"] >= g["to_ms"]]
        assert not early, f"trade rows at/after {g['kind']} to_ms={g['to_ms']} were written before its gap row"


def src_with_links(feed: af.Feed):
    src = af.build_source("pumpswap", ["wss://a.test"], 2, "confirmed", stagger_s=0.0)
    feed.sources["pumpswap"] = src
    for s in src.socket_stats:
        s.link.mark_up(T)
    return src


def drop_and_reconnect(sockets, t_down: int, t_up: int) -> None:
    for s in sockets:
        s.link.mark_down(t_down)
        s.reconnects += 1
        s.closes[1006] = s.closes.get(1006, 0) + 1
    for s in sockets:
        s.link.mark_up(t_up)


@pytest.mark.parametrize("both_sockets, data_loss", [(True, True), (False, False)])
def test_a_socket_reconnect_gap_precedes_the_first_trade_after_it(tmp_path: Path, both_sockets: bool, data_loss: bool) -> None:
    """No tick() runs between the reconnect and the trades: the gap must still be written first (it used to wait for the 5 s housekeeping tick)."""
    clock = {"t": T}
    feed = make_feed(tmp_path, clock)
    src = src_with_links(feed)
    feed.handle_notice("pumpswap", notice(100, T + 500))
    drop_and_reconnect(src.socket_stats if both_sockets else src.socket_stats[:1], T + 1000, T + 4000)
    feed.handle_notice("pumpswap", notice(101, T + 4100))  # the first notice after the reconnect
    feed.handle_notice("pumpswap", notice(102, T + 4200))
    feed.writer.close()
    rows = tape(tmp_path)
    assert shape(rows) == ["trade", "socket_reconnect", "trade", "trade"]
    gap = next(r for r in rows if r["type"] == "gap" and r["prog"] == "pumpswap")
    assert gap["data_loss"] is data_loss and gap["kind"] == "socket_reconnect" and gap["t_recv_ms"] <= T + 4100
    if data_loss:
        assert (gap["from_ms"], gap["to_ms"]) == (T + 1000, T + 4000)  # the common outage
    assert_gap_first(rows)


def test_a_slot_jump_gap_precedes_the_trade_of_the_notice_that_jumped(tmp_path: Path) -> None:
    feed = make_feed(tmp_path, {"t": T})
    feed.handle_notice("pumpswap", notice(100, T + 1000))
    feed.handle_notice("pumpswap", notice(500, T + 9000))
    feed.writer.close()
    rows = tape(tmp_path)
    assert shape(rows) == ["trade", "slot_jump", "trade"]
    assert_gap_first(rows)


def test_a_closed_silence_gap_precedes_the_first_trade_after_the_silence(tmp_path: Path) -> None:
    clock = {"t": T}
    feed = make_feed(tmp_path, clock)
    feed.handle_notice("pumpswap", notice(100, T + 1000))
    clock["t"] = T + 22_000
    feed.tick(clock["t"])  # silence detected: the open record
    feed.handle_notice("pumpswap", notice(101, T + 40_000))
    feed.writer.close()
    rows = tape(tmp_path)
    assert shape(rows) == ["trade", "silence/open", "silence/closed", "trade"]
    assert_gap_first(rows)


def test_a_closed_feed_restart_gap_precedes_the_first_trade_of_the_new_source(tmp_path: Path) -> None:
    clock = {"t": T}
    feed = make_feed(tmp_path, clock)
    feed.handle_notice("pumpswap", notice(100, T + 1000))
    feed.emit_safe(feed.watch["pumpswap"].feed_restart(T + 20_000, "source_restart"))  # what pump() does after the backoff
    feed.handle_notice("pumpswap", notice(9000, T + 23_000))
    feed.writer.close()
    rows = tape(tmp_path)
    assert shape(rows) == ["trade", "feed_restart/open", "feed_restart/closed", "trade"]
    assert_gap_first(rows)


def test_the_reconnect_gap_is_written_once_not_again_by_the_tick(tmp_path: Path) -> None:
    clock = {"t": T}
    feed = make_feed(tmp_path, clock)
    src = src_with_links(feed)
    feed.handle_notice("pumpswap", notice(100, T + 500))
    drop_and_reconnect(src.socket_stats, T + 1000, T + 4000)
    feed.handle_notice("pumpswap", notice(101, T + 4100))
    clock["t"] = T + 4200
    feed.tick(clock["t"])  # the 5 s tick sees the same counters: nothing more to write
    feed.writer.close()
    assert sum(1 for r in tape(tmp_path) if r["type"] == "gap" and r["kind"] == "socket_reconnect") == 1
