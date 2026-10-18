"""Review item 10: every gap row has an explicit span [from_ms, to_ms]; silence and feed restart also close. No network."""

from __future__ import annotations

import json
from pathlib import Path

from observe.fake_ws import fixture_line
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.test_arena_feed import roomy

T = 1_790_316_000_000


def spans(rows: list[dict]) -> list[tuple]:
    return [(r["kind"], r["from_ms"], r["to_ms"], r.get("closed")) for r in rows]


def test_slot_jump_span_runs_from_the_last_high_water_notice() -> None:
    w = af.ProgWatch("pumpswap", T)
    w.on_notice(100, T + 1000)
    w.on_notice(101, T + 2000)
    w.on_notice(99, T + 2500)  # disorder: does not move the high-water mark or its time
    (g,) = w.on_notice(300, T + 9000)
    assert (g["kind"], g["from_ms"], g["to_ms"], g["from_slot"], g["to_slot"], g["missed_slots"], g["data_loss"]) == ("slot_jump", T + 2000, T + 9000, 101, 300, 198, True)


def test_silence_is_written_open_then_closed_with_its_end() -> None:
    w = af.ProgWatch("pumpfun", T)
    w.on_notice(1, T + 1000)
    (open_,) = w.tick(T + 1000 + 21_000)
    assert spans([open_]) == [("silence", T + 1000, T + 22_000, False)] and open_["silent_ms"] == 21_000
    assert w.tick(T + 30_000) == []  # once per episode
    out = w.on_notice(2, T + 45_000)
    assert spans(out) == [("silence", T + 1000, T + 45_000, True)] and out[0]["silent_ms"] == 44_000
    assert w.on_notice(3, T + 46_000) == []  # the episode is over


def test_feed_restart_span_ends_at_the_first_notice_of_the_new_source() -> None:
    w = af.ProgWatch("pumpswap", T)
    w.on_notice(10, T + 5000)
    rec = w.feed_restart(T + 20_000, "source_restart")  # stamped after the backoff
    assert spans([rec]) == [("feed_restart", T + 5000, T + 20_000, False)] and w.hw_slot is None
    out = w.on_notice(50_000, T + 23_000)  # the new source's first notice: far from the old slot, but no slot_jump (hw reset)
    assert spans(out) == [("feed_restart", T + 5000, T + 23_000, True)]
    assert w.on_notice(50_001, T + 23_100) == []


def test_a_second_restart_before_any_notice_keeps_the_first_start() -> None:
    w = af.ProgWatch("pumpswap", T)
    w.on_notice(10, T + 5000)
    w.feed_restart(T + 20_000, "a")
    rec = w.feed_restart(T + 40_000, "b")
    assert rec["from_ms"] == T + 5000 and rec["to_ms"] == T + 40_000


def test_reconnect_span_is_the_common_outage_when_link_state_knows_it() -> None:
    w = af.ProgWatch("pumpswap", T)
    snap = {"reconnects": 0, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0], "sockets": 1}
    w.note_feed_stats(snap, T)
    (g,) = w.note_feed_stats({**snap, "reconnects": 1, "per_socket": [1]}, T + 3000)  # no link state: the span is the look time
    assert (g["kind"], g["from_ms"], g["to_ms"]) == ("socket_reconnect", T + 3000, T + 3000)


def test_spans_reach_the_tape_through_the_feed(tmp_path: Path) -> None:
    clock = {"t": T}
    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: clock["t"], rpc_url=None, disk_usage=roomy, compress=False)
    n = lambda slot, t: RawNotice(slot=slot, signature=f"{slot:088d}", failed=False, logs=(fixture_line("pumpswap_buy_event.b64"),), t_recv_ms=t, commitment="c", feed="f")  # noqa: E731
    feed.handle_notice("pumpswap", n(100, T + 1000))
    clock["t"] = T + 1000 + 21_000
    feed.tick(clock["t"])  # silence detected
    feed.handle_notice("pumpswap", n(101, T + 40_000))  # the feed delivers again
    feed.writer.close()
    gaps = [r for f in sorted(tmp_path.glob("tape-*.jsonl")) for r in map(json.loads, f.read_text().splitlines()) if r["type"] == "gap" and r["prog"] == "pumpswap"]
    assert spans(gaps) == [("silence", T + 1000, T + 22_000, False), ("silence", T + 1000, T + 40_000, True)]
