"""Review item 4: ArenaState gap accounting does not stop counting, and keeps the spans. No network."""

from __future__ import annotations

from pathlib import Path

from tools import arena_state as ast_
from tools.arena_state import ArenaState
from tools.test_arena_state import M, curve

T = 1_790_316_000_000
MIN = 60_000


def gap(prog: str, kind: str, fr: int, to: int, loss: bool = True, t: int | None = None, **kw) -> dict:
    return {"v": 1, "type": "gap", "prog": prog, "slot": 0, "t_recv_ms": t if t is not None else to, "signature": None, "event_index": None, "kind": kind,
            "data_loss": loss, "from_ms": fr, "to_ms": to, **kw}


def test_the_count_keeps_growing_past_the_old_2000_row_deque(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    for i in range(2500):
        st.apply(gap("pumpfun", "slot_jump", T + i * 10, T + i * 10 + 5))
    st.apply(curve(T + 100_000, "A", "buy", 1.0, 1e-7))  # the key is first seen after 2,500 gaps
    for i in range(100):
        st.apply(gap("pumpfun", "slot_jump", T + 200_000 + i * 10, T + 200_000 + i * 10 + 5))
    assert st.gaps_total["pumpfun"] == 2600 and st.snapshot(M)["gaps_since_first_seen"] == 100  # the old code said 0 here


def test_an_open_and_its_closed_record_are_one_span_and_the_fields_survive(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    st.apply(gap("pumpswap", "silence", T + 1000, T + 22_000, closed=False, silent_ms=21_000))
    st.apply(gap("pumpswap", "silence", T + 1000, T + 45_000, closed=True, silent_ms=44_000))
    st.apply(gap("pumpswap", "slot_jump", T + 50_000, T + 60_000, from_slot=10, to_slot=90, missed_slots=79))
    assert st.gaps_total["pumpswap"] == 2 and st.loss_spans["pumpswap"] == 2
    s1, s2 = st.gap_spans()
    assert (s1["kind"], s1["from_ms"], s1["to_ms"], s1["closed"], s1["silent_ms"]) == ("silence", T + 1000, T + 45_000, True, 44_000)
    assert (s2["kind"], s2["from_slot"], s2["to_slot"], s2["missed_slots"]) == ("slot_jump", 10, 90, 79)


def test_gap_spans_answer_does_the_decision_window_hold_a_gap(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    st.apply(gap("pumpswap", "slot_jump", T + 10 * MIN, T + 11 * MIN))
    st.apply(gap("pumpfun", "slot_jump", T + 30 * MIN, T + 31 * MIN))
    st.apply(gap("all", "process_restart", T + 50 * MIN, T + 52 * MIN))
    st.apply(gap("pumpswap", "socket_reconnect", T + 20 * MIN, T + 20 * MIN, loss=False))
    assert not st.has_gap(T, T + 9 * MIN, "pumpswap") and st.has_gap(T, T + 10 * MIN, "pumpswap")  # inclusive at the span's start
    assert st.has_gap(T + 11 * MIN, T + 12 * MIN, "pumpswap") and not st.has_gap(T + 11 * MIN + 1, T + 12 * MIN, "pumpswap")  # and at its end
    assert not st.has_gap(T + 30 * MIN, T + 31 * MIN, "pumpswap") and st.has_gap(T + 30 * MIN, T + 31 * MIN, "pumpfun")  # another program's gap
    assert st.has_gap(T + 51 * MIN, T + 51 * MIN, "pumpswap") and st.has_gap(T + 51 * MIN, T + 51 * MIN, "pumpfun")  # a restart hits both
    assert not st.has_gap(T + 20 * MIN, T + 20 * MIN, "pumpswap")  # a reconnect that a peer covered is not data loss
    assert [s["kind"] for s in st.gap_spans(T + 20 * MIN, T + 20 * MIN, "pumpswap", data_loss_only=False)] == ["socket_reconnect"]


def test_a_disk_reserve_row_spans_from_paused_since(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    row = gap("all", "disk_reserve", T, T + 5 * MIN, t=T + 5 * MIN)
    del row["from_ms"], row["to_ms"]
    row["paused_since_ms"] = T + MIN
    st.apply(row)
    (s,) = st.gap_spans()
    assert (s["from_ms"], s["to_ms"]) == (T + MIN, T + 5 * MIN)


def test_all_feed_gaps_count_for_a_key_first_seen_before_them(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    st.apply(curve(T, "A", "buy", 1.0, 1e-7))
    st.apply(gap("all", "process_restart", T + MIN, T + 2 * MIN))
    st.apply(gap("pumpswap", "slot_jump", T + 3 * MIN, T + 4 * MIN))  # another program's feed: not this key's
    assert st.snapshot(M)["gaps_since_first_seen"] == 1 and st.snapshot(M)["gap_span_retention_h"] == 24.0


def test_old_spans_are_pruned_but_the_counts_stay(tmp_path: Path) -> None:
    st = ArenaState(tmp_path, start_ms=0)
    st.apply(gap("pumpfun", "slot_jump", T, T + MIN))
    st.apply(curve(T + ast_.GAP_KEEP_MS + 10 * MIN, "A", "buy", 1.0, 1e-7))
    st.sweep()
    assert st.gap_spans() == [] and st.gaps_total["pumpfun"] == 1 and st.loss_spans["pumpfun"] == 1
