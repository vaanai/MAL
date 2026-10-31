"""Tests for tools/r4b_host_glue.py. SYNTHETIC rows, synthetic tape files in tmp dirs and FAKE sockets only: no network, no key, no lab tape, no sealed path.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages systemd-run --user --scope -q -p MemoryMax=8G /data/mal/venv/bin/python -s -m pytest \
      tools/test_r4b_host_glue.py tools/test_r4b_feed_merge.py tools/test_r4b_feed_adapter.py -q
  (if duckdb is missing there: /data/mal/c1nf-parity-venv/bin/python; the engine tests skip without duckdb)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

from tools import r4b_feed_adapter as fa
from tools import r4b_feed_merge as fm
from tools import r4b_host_glue as hg
from tools.test_r4b_feed_merge import FAKE_KEY, Clock, FakeConnect, FakeWS, T, ack, logs_msg, make_recv, script, slot_msg  # noqa: F401

PUB, HEL = hg.SOURCE_PUBLIC, hg.SOURCE_HELIUS
ROOT = Path(__file__).resolve().parents[1]
WSOL = "So11111111111111111111111111111111111111112"
T0 = 1_786_665_600


# ----------------------------------------------------------------------------------------------------------------------------------- rows
def bd(slot: int, sig: str, ts: int | None = None, mint: str = "MINT", t_recv: int | None = None, **kw) -> dict:
    ts = T0 + slot if ts is None else ts
    return {"v": 1, "type": "trade", "prog": "pumpfun", "slot": slot, "t_recv_ms": ts * 1000 if t_recv is None else t_recv, "signature": sig, "event_index": 0,
            "venue": "pump_bonding", "mint": mint, "mint_source": "event", "trader": "TR", "side": "buy", "sol_lamports": 10**9, "token_raw": 10**6, "event_ts": ts,
            "pool": None, "quote_reserve": 30 * 10**9, "base_reserve": 10**15, "quote_mint": WSOL, **kw}


def ps(slot: int, sig: str, ts: int, mint: str | None = "MINT", pool: str = "POOL", **kw) -> dict:
    return {"v": 1, "type": "trade", "prog": "pumpswap", "slot": slot, "t_recv_ms": ts * 1000 + 5, "signature": sig, "event_index": 0, "venue": "pumpswap",
            "mint": mint, "mint_source": "create_pool", "trader": "TR", "side": "buy", "sol_lamports": 10**9, "token_raw": 10**6, "event_ts": ts, "pool": pool,
            "quote_reserve": 85 * 10**9, "base_reserve": 206 * 10**12, "quote_mint": WSOL, "virtual_quote_reserves": 17_600_000_000, "ix_name": "buy", **kw}


def gap(kind: str, *, closed: bool | None = None, data_loss: bool | None = True, prog: str = "pumpswap", from_ms: int = 1, to_ms: int = 2, **kw) -> dict:
    r = {"v": 1, "type": "gap", "prog": prog, "slot": 0, "t_recv_ms": to_ms, "signature": None, "event_index": None, "kind": kind, "from_ms": from_ms,
         "to_ms": to_ms, **kw}
    if data_loss is not None:
        r["data_loss"] = data_loss
    if closed is not None:
        r["closed"] = closed
    return r


def keys_of(rows) -> list:
    return [fm.row_key(r) for r in rows if fm.row_key(r) is not None]


def feed_public(m: hg.HostMerger, slots, prefix="p") -> list:
    return [e for s in slots if (e := m.offer(PUB, bd(s, f"{prefix}{s}"), 1)) is not None]


# ----------------------------------------------------------------------------------------------------------------------------------- G1
def public_lag_scenario(m) -> tuple[list, set]:
    """The G1 case: a Helius window at slots 5000.., another an hour later at 30000.., the public stream stuck at slot 119 (prefix), then catching up in bulk over
    both windows and the history between. Returns (emitted rows in arrival order, the set of unique keys that exist)."""
    out, unique = [], set()

    def go(src, row, note=None):
        unique.add(fm.row_key(row))
        e = m.offer(src, row, 1)
        if e is not None:
            out.append(e)

    for s in range(100, 120):
        go(PUB, bd(s, f"h{s}"))
    w1 = [bd(s, f"w1_{s}") for s in range(5000, 5020)]
    for r in w1:
        go(HEL, r)
    if hasattr(m, "note_window"):
        m.note_window(4990, 5100)
    w2 = [bd(s, f"w2_{s}") for s in range(30000, 30020)]
    for r in w2:
        go(HEL, r)
    if hasattr(m, "note_window"):
        m.note_window(29990, 30100)
    for r in w1:  # the public bulk catch-up: window-covered history, history nobody else saw, the later window, the newest rows
        go(PUB, {**r, "t_recv_ms": r["t_recv_ms"] + 7})
    for s in range(6000, 6010):
        go(PUB, bd(s, f"only{s}"))
    for r in w2:
        go(PUB, {**r, "t_recv_ms": r["t_recv_ms"] + 7})
    for s in range(31000, 31010):
        go(PUB, bd(s, f"late{s}"))
    return out, unique


def test_plain_merger_double_counts_after_a_long_public_lag_and_host_merger_does_not():
    base_out, unique = public_lag_scenario(fm.Merger(horizon_slots=1500))
    assert len(base_out) > len(unique), "the defect G1 names: window rows are emitted again by the late public copy"
    host = hg.HostMerger(horizon_slots=1500)
    out, unique = public_lag_scenario(host)
    ks = keys_of(out)
    assert len(ks) == len(set(ks)) == len(unique)  # re-emits nothing: every key once
    assert set(ks) == unique
    assert host.stats["dup_helius_then_public"] == 40  # all 40 window rows met their public copy
    assert host.stats["helius_only_released"] == 0
    assert host.public_hw_slot() == host.pub_top == 31009
    assert host.counts()["below_horizon"] == 10  # only the 10 history rows nobody covered were below the horizon (and are rightly emitted)
    assert not host.flagged and not host._held


def test_engine_state_after_the_lagging_public_stream_equals_the_single_feed():
    """Clock, market minutes and member prints of an Engine fed through the adapter: merged (window first, public later in bulk) = the single public feed."""
    pytest.importorskip("duckdb")
    import duckdb

    from tools import r4b_live as lv

    con = duckdb.connect()
    hf = lambda x: int(con.execute("select hash(?::varchar)", [x]).fetchone()[0])  # noqa: E731
    mk = lambda: lv.Engine(universe_min_mbt=0, pda_fn=lambda m: "POOL" if m == "MINT" else "P_" + m, hash_fn=hf, score_fn=lambda b, X: X[:, 0])  # noqa: E731

    def stream(m) -> list:
        head = [{"v": 1, "type": "complete", "prog": "pumpfun", "slot": 91, "t_recv_ms": 1, "signature": "sc", "event_index": 0, "mint": "MINT", "trader": "x",
                 "bonding_curve": "BC", "event_ts": T0 - 5, "quote_mint": WSOL},
                {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 95, "t_recv_ms": 2, "signature": "sp", "event_index": 0, "pool": "POOL", "mint": "MINT",
                 "quote_mint": WSOL, "event_ts": T0 - 4}]
        out = [e for r in head if (e := m.offer(PUB, r, 1)) is not None]
        mkrow = lambda s, sig, ts: ps(s, sig, ts)  # noqa: E731
        w1 = [mkrow(5000 + i, f"w1_{i}", T0 + 1800 + i) for i in range(20)]
        w2 = [mkrow(30000 + i, f"w2_{i}", T0 + 7200 + i) for i in range(20)]
        pre = [mkrow(100 + i, f"h{i}", T0 + i) for i in range(20)]
        mid = [mkrow(6000 + i, f"o{i}", T0 + 2000 + i) for i in range(10)]
        late = [mkrow(31000 + i, f"l{i}", T0 + 7300 + i) for i in range(10)]
        for r in pre:
            out += [e for e in [m.offer(PUB, r, 1)] if e]
        for r in w1:
            out += [e for e in [m.offer(HEL, r, 1)] if e]
        if hasattr(m, "note_window"):
            m.note_window(4990, 5100)
        for r in w2:
            out += [e for e in [m.offer(HEL, r, 1)] if e]
        if hasattr(m, "note_window"):
            m.note_window(29990, 30100)
        for r in w1 + mid + w2 + late:
            out += [e for e in [m.offer(PUB, r, 1)] if e]
        return out

    def run(rows) -> lv.Engine:
        eng, ad = mk(), fa.FeedAdapter()
        for r in rows:
            fa.apply(eng, ad.feed(r))
        return eng

    merged = run(stream(hg.HostMerger(horizon_slots=1500)))
    # the single feed: every unique row once, in slot order (the public stream as it would have arrived without a lag)
    ref_rows = {}
    for r in stream(hg.HostMerger(horizon_slots=1500)):
        ref_rows[fm.row_key(r)] = r
    single = run(sorted(ref_rows.values(), key=lambda r: (r["slot"], r["signature"])))
    assert merged.clock == single.clock and merged.mkt == single.mkt and merged.ncr == single.ncr
    assert sorted(merged.mints["MINT"].prints["slot"].tolist()) == sorted(single.mints["MINT"].prints["slot"].tolist())
    assert len(merged.mints["MINT"].prints["slot"]) == 20 + 10 + 20 + 20 + 10  # nothing counted twice, nothing lost
    # and the plain Merger on the same arrival order does count the window rows twice in the market minutes
    plain = run(public_lag_plain_rows())
    assert sum(v[0] for v in plain.mkt.values()) > sum(v[0] for v in merged.mkt.values())


def public_lag_plain_rows() -> list:
    """The same arrival order as test_engine_state..., through the plain library Merger (the defect)."""
    m = fm.Merger(horizon_slots=1500)
    out = [e for r in [{"v": 1, "type": "complete", "prog": "pumpfun", "slot": 91, "t_recv_ms": 1, "signature": "sc", "event_index": 0, "mint": "MINT", "trader": "x",
                        "bonding_curve": "BC", "event_ts": T0 - 5, "quote_mint": WSOL},
                       {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 95, "t_recv_ms": 2, "signature": "sp", "event_index": 0, "pool": "POOL",
                        "mint": "MINT", "quote_mint": WSOL, "event_ts": T0 - 4}] if (e := m.offer(PUB, r, 1)) is not None]
    pre = [ps(100 + i, f"h{i}", T0 + i) for i in range(20)]
    w1 = [ps(5000 + i, f"w1_{i}", T0 + 1800 + i) for i in range(20)]
    w2 = [ps(30000 + i, f"w2_{i}", T0 + 7200 + i) for i in range(20)]
    mid = [ps(6000 + i, f"o{i}", T0 + 2000 + i) for i in range(10)]
    late = [ps(31000 + i, f"l{i}", T0 + 7300 + i) for i in range(10)]
    for src, rows in ((PUB, pre), (HEL, w1), (HEL, w2), (PUB, w1 + mid + w2 + late)):
        out += [e for r in rows if (e := m.offer(src, r, 1)) is not None]
    return out


def test_a_public_row_below_the_horizon_inside_a_covered_range_is_data_loss_not_a_second_emit():
    m = hg.HostMerger(horizon_slots=1500)
    feed_public(m, range(100, 120))
    assert m.offer(HEL, bd(5000, "w1"), 1) is not None
    m.note_window(4990, 5100)
    assert m.offer(HEL, bd(9000, "w2"), 1) is not None  # the newest slot of either source moves 4000 slots on
    m.note_window(8990, 9100)
    assert m.public_hw_slot() == 119
    # a row of slot 5001 the window never saw, arriving from the public stream after the horizon passed it: covered range, absent key
    assert m.offer(PUB, bd(5001, "never_seen_by_window"), 2) is None
    assert m.stats["below_horizon_covered"] == 1 and m.stats["emitted_public"] == 20
    g = [x for x in m.flagged if x["why"] == "below_horizon_covered"]
    assert len(g) == 1 and (g[0]["lo"], g[0]["hi"]) == (5001, 5001)
    assert m.public_hw_slot() == 5000  # the seam is open there: the public stream is not "complete" up to its own top
    c = fm.mark_complete(T, 5050, _armed_state(T, cov=4990, tip_to=5100), public_hw_slot=m.public_hw_slot())
    assert c.reason == "seam_open" and not c.complete
    # the same shape OUTSIDE any window-covered range is an ordinary history row: emitted, counted below_horizon
    assert m.offer(PUB, bd(3000, "plain_history"), 2) is not None and m.counts()["below_horizon"] >= 1


def test_a_held_key_evicted_by_the_key_cap_does_not_let_its_public_copy_through():
    m = hg.HostMerger(horizon_slots=1500, max_keys=10)
    feed_public(m, range(100, 105))
    for s in range(5000, 5030):
        m.offer(HEL, bd(s, f"w{s}"), 1)
    m.note_window(4990, 5100)
    assert m.stats["held_evicted_cap"] > 0 and m.unsafe_below >= 5000
    m.offer(HEL, bd(20000, "far"), 1)
    # the evicted (oldest) window keys are gone; the public copy of one of them must not be emitted again
    assert m.offer(PUB, bd(5000, "w5000"), 2) is None
    assert m.stats["below_horizon_covered"] >= 1


def test_helius_first_key_is_released_only_when_the_public_stream_has_passed_it():
    m = hg.HostMerger(horizon_slots=1500, public_margin_slots=10)
    feed_public(m, range(100, 110))
    m.offer(HEL, bd(5000, "w"), 1)
    m.offer(HEL, bd(9000, "w2"), 1)  # either-source horizon passed slot 5000 long ago ...
    assert ("w", "trade", 0) in m._keys  # ... but the public stream is at 109: the key stays
    feed_public(m, range(110, 5011))
    assert m.stats["dup_helius_then_public"] == 1 and ("w", "trade", 0) not in m._held
    # a Helius-first key the public stream passes without ever delivering it is released and counted (a fork candidate or a public loss)
    m.offer(HEL, bd(5100, "orphan"), 1)
    feed_public(m, range(5011, 5200), prefix="q")
    m.offer(PUB, bd(5300, "x"), 1)
    assert m.stats["helius_only_released"] == 1


# ----------------------------------------------------------------------------------------------------------------------------------- G2
def test_slot_jump_gap_holds_the_high_water_and_the_range_is_flagged_when_it_ages_out():
    m = hg.HostMerger(gap_guard_slots=1500)
    feed_public(m, range(90, 101))
    assert m.public_hw_slot() == 100
    m.offer(PUB, gap("slot_jump", from_slot=100, to_slot=200, missed_slots=99), 1)
    feed_public(m, [200, 201, 250])
    assert m.pub_top == 250 and m.public_hw_slot() == 100  # held at the gap's start
    g = m.open_gaps()[0]
    assert (g["lo"], g["hi"], g["state"]) == (101, 199, "closed")
    feed_public(m, range(251, 1700), prefix="r")
    assert m.public_hw_slot() == 100  # 1449 slots of progress since 250 < 1500
    feed_public(m, [1750, 1751], prefix="s")
    assert m.public_hw_slot() == 1751 and not m.open_gaps()
    f = [x for x in m.flagged if x["kind"] == "slot_jump"][0]
    assert f["ended"] == "aged_flagged" and (f["lo"], f["hi"]) == (101, 199)  # permanently flagged, never silently forgotten
    assert m.host_counts()["lost_range_ends"] == {"aged_flagged": 1}


def test_silence_pair_open_then_closed_holds_until_a_window_covers_the_range():
    m = hg.HostMerger()
    feed_public(m, range(90, 101))
    m.offer(PUB, gap("silence", closed=False, from_ms=5, to_ms=6), 1)
    feed_public(m, [130, 131], prefix="o")  # the other program keeps delivering: still held at 100
    assert m.public_hw_slot() == 100 and m.open_gaps()[0]["state"] == "open"
    m.offer(PUB, gap("silence", closed=True, from_ms=5, to_ms=9), 1)
    assert m.open_gaps()[0]["state"] == "resume" and m.public_hw_slot() == 100
    feed_public(m, [150])  # the first row above the hold ... (131 was already above it, so the next row closes it at 130? no: the range ends below this row)
    g = m.open_gaps()[0]
    assert g["state"] == "closed" and g["lo"] == 101 and g["hi"] == 149 and m.public_hw_slot() == 100
    m.note_window(120, 400)  # a window that starts INSIDE the range does not cover it
    assert m.public_hw_slot() == 100
    m.note_window(95, 400)  # a window that covers [101, 149] releases the hold
    assert m.public_hw_slot() == 150 and not m.open_gaps()
    assert [x["ended"] for x in m.flagged] == ["covered"]


def test_gaps_that_hold_nothing():
    m = hg.HostMerger()
    m.offer(PUB, gap("process_restart", prog="all"), 1)  # before any data row: no history to hold
    assert m.stats["pub_gap_before_data"] == 1 and m.public_hw_slot() is None
    feed_public(m, range(10, 20))
    m.offer(PUB, gap("socket_reconnect", data_loss=False), 1)  # a peer socket covered the drop
    feed_public(m, [30], prefix="n")
    assert m.public_hw_slot() == 30 and m.stats["pub_gap_benign"] == 1 and not m.flagged
    m.offer(PUB, gap("slot_jump", data_loss=None, from_slot=30, to_slot=50), 1)  # data_loss missing: fail closed
    assert m.public_hw_slot() == 30 and len(m.open_gaps()) == 1


def test_error_kinds_name_their_lost_slots_and_gaps_without_a_closing_record_hold_for_good():
    m = hg.HostMerger()
    feed_public(m, range(100, 151))
    m.offer(PUB, gap("decode_error", from_slot=120, to_slot=130, count=3), 1)
    assert m.public_hw_slot() == 119 and (m.open_gaps()[0]["lo"], m.open_gaps()[0]["hi"]) == (120, 130)
    m.note_window(110, 200)
    assert m.public_hw_slot() == 150
    m.offer(PUB, gap("feed_restart", closed=False), 1)  # never closed: a dead feed
    feed_public(m, range(151, 5000), prefix="z")
    assert m.public_hw_slot() == 150 and m.open_gaps()[0]["state"] == "open"


def _armed_state(mark: int, *, cov: int, tip_to: int) -> fm.WindowState:
    st = fm.WindowState(mark)
    st.connected_ms = (mark - 30) * 1000
    st.armed_ms = st.connected_ms + 100
    st.tips = [(st.armed_ms + 100 + 400 * i, cov - 1 + i) for i in range(tip_to - cov + 2)]
    return st


def test_seam_stays_open_over_a_gap_range_in_mark_complete_and_closes_when_it_is_covered():
    m = hg.HostMerger()
    feed_public(m, range(900, 1000))
    m.offer(PUB, gap("slot_jump", from_slot=999, to_slot=1010), 1)  # range [1000, 1009] lies below the window's coverage start 1020
    feed_public(m, range(1010, 1030), prefix="b")
    st = _armed_state(T, cov=1020, tip_to=1120)
    c = fm.mark_complete(T, 1060, st, public_hw_slot=m.public_hw_slot())
    assert c.complete is False and c.reason == "seam_open" and m.public_hw_slot() == 999
    # a gap whose range is INSIDE the window's coverage does not keep the seam open
    m2 = hg.HostMerger()
    feed_public(m2, range(900, 1030))
    m2.note_window(1020, 1119)
    m2.offer(PUB, gap("slot_jump", from_slot=1030, to_slot=1045), 1)
    feed_public(m2, range(1045, 1050), prefix="b")
    assert fm.mark_complete(T, 1060, st, public_hw_slot=m2.public_hw_slot()).complete is True and m2.flagged[0]["ended"] == "covered"


# ----------------------------------------------------------------------------------------------------------------------------------- the tape tail
def write_lines(path: Path, rows, *, tail: bytes = b"") -> None:
    path.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows) + tail)


def test_tail_reads_the_open_hour_by_offset_and_waits_for_a_torn_last_line(tmp_path: Path):
    H = T0 // 3600 * 3600
    p = tmp_path / f"tape-{hg.hour_key(H)}.jsonl"
    rows = [bd(100 + i, f"a{i}") for i in range(5)]
    line6 = json.dumps(bd(105, "a5")).encode() + b"\n"
    write_lines(p, rows, tail=line6[:20])
    tail = hg.TapeTail(tmp_path, H, clock_ms=lambda: H * 1000 + 60_000)
    assert [r["signature"] for r in tail.poll()] == [f"a{i}" for i in range(5)]
    assert tail.poll() == []  # the torn line is not consumed
    with open(p, "ab") as f:
        f.write(line6[20:])
        f.write(json.dumps(bd(106, "a6")).encode() + b"\n")
    assert [r["signature"] for r in tail.poll()] == ["a5", "a6"]
    assert tail.stats["bad_lines"] == 0


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs the zstd CLI")
def test_tail_catches_up_through_closed_zst_hours_and_continues_in_the_open_hour(tmp_path: Path):
    H = T0 // 3600 * 3600
    for i, name in ((0, "zst"), (1, "zst")):
        p = tmp_path / f"tape-{hg.hour_key(H + i * 3600)}.jsonl"
        write_lines(p, [bd(i * 1000 + j, f"h{i}_{j}", ts=H + i * 3600 + j) for j in range(50)])
        subprocess.run(["zstd", "-q", "-9", "--rm", str(p)], check=True)
    cur = tmp_path / f"tape-{hg.hour_key(H + 7200)}.jsonl"
    write_lines(cur, [bd(2000 + j, f"h2_{j}") for j in range(30)])
    tail = hg.TapeTail(tmp_path, H, clock_ms=lambda: (H + 7200) * 1000 + 30_000, final_after_s=125)
    got = []
    while True:
        b = tail.poll(limit=40)
        if not b:
            break
        got += b
    assert [r["signature"] for r in got] == [f"h0_{j}" for j in range(50)] + [f"h1_{j}" for j in range(50)] + [f"h2_{j}" for j in range(30)]
    assert tail.hour == H + 7200 and tail.stats["zst_error"] == 0
    # the open hour is compacted under the reader: the .zst is read from the same byte offset, nothing twice
    with open(cur, "ab") as f:
        f.write(json.dumps(bd(2030, "h2_30")).encode() + b"\n")
    assert [r["signature"] for r in tail.poll()] == ["h2_30"]
    subprocess.run(["zstd", "-q", "-9", "--rm", str(cur)], check=True)
    assert tail.poll() == []
    cur2 = tmp_path / f"tape-{hg.hour_key(H + 7200)}.jsonl.zst"
    assert cur2.exists()


def test_tail_missing_hour_and_bad_lines_become_data_loss_gap_rows(tmp_path: Path):
    H = T0 // 3600 * 3600
    write_lines(tmp_path / f"tape-{hg.hour_key(H)}.jsonl", [bd(1, "a")], tail=b"")
    write_lines(tmp_path / f"tape-{hg.hour_key(H + 7200)}.jsonl", [bd(2, "c")])
    now = {"ms": (H + 3600) * 1000 + 1000}
    tail = hg.TapeTail(tmp_path, H, clock_ms=lambda: now["ms"], missing_grace_s=600, early_settle_s=2)
    assert [r["signature"] for r in tail.poll()] == ["a"]
    assert tail.poll() == [] and tail.hour == H  # hour H is not final yet
    now["ms"] = (H + 3600 + 130) * 1000
    assert tail.poll() == [] and tail.hour == H + 3600  # H is final; H+1 is absent but not yet "missing" (grace)
    now["ms"] = (H + 3600 + 700) * 1000
    out = tail.poll()
    g = [r for r in out if r["type"] == "gap"]
    assert len(g) == 1 and g[0]["kind"] == "tape_hour_missing" and g[0]["data_loss"] is True and g[0]["closed"] is True
    assert [r["signature"] for r in out if r["type"] == "trade"] == ["c"]
    # a burst of bad lines is one gap row; a NUL line is bad
    p = tmp_path / f"tape-{hg.hour_key(H + 10800)}.jsonl"
    p.write_bytes(json.dumps(bd(3, "d")).encode() + b"\n" + b"\x00\x00\x00\n" + b"not json\n" + b"[1]\n" + json.dumps(bd(4, "e")).encode() + b"\n")
    now["ms"] = (H + 10800 + 10) * 1000
    out = tail.poll()
    assert [r["signature"] for r in out if r["type"] == "trade"] == ["d", "e"]
    assert [r["kind"] for r in out if r["type"] == "gap"] == ["tape_bad_line"] and tail.stats["bad_lines"] == 3


def test_tail_leaves_an_hour_early_when_the_next_hour_exists_and_reads_late_lines_once_it_is_final(tmp_path: Path):
    H = T0 // 3600 * 3600
    a, b = tmp_path / f"tape-{hg.hour_key(H)}.jsonl", tmp_path / f"tape-{hg.hour_key(H + 3600)}.jsonl"
    write_lines(a, [bd(1, "a1")])
    write_lines(b, [bd(2, "b1")])
    now = {"ms": (H + 3600 + 5) * 1000}
    tail = hg.TapeTail(tmp_path, H, clock_ms=lambda: now["ms"])
    assert [r["signature"] for r in tail.poll()] == ["a1", "b1"]  # the new hour is read 5 s after the boundary, not 125 s
    with open(a, "ab") as f:  # the writer still appends a late row to the closed hour
        f.write(json.dumps(bd(1, "a2")).encode() + b"\n")
    assert tail.poll() == []
    now["ms"] = (H + 3600 + 130) * 1000
    assert [r["signature"] for r in tail.poll()] == ["a2"] and tail.stats["trail_rows"] == 1
    assert tail.poll() == []


# ----------------------------------------------------------------------------------------------------------------------------------- LiveSource
def make_source(tmp_path: Path, *, public_to: int = 1010, extra_public=(), mirror=None, cap_credits: float = 50_000, spawn=None, **kw):
    """A LiveSource on a synthetic tape (hour T - 3600) and a fake window that plays the whole [T - 30 s, T + 10 s] script when it is started (inline)."""
    tape = tmp_path / "tape"
    tape.mkdir(exist_ok=True)
    rows = [bd(s, f"P{s}", ts=T - 3600 + (s - 900) // 5, t_recv=(T - 3600) * 1000 + (s - 900) * 100) for s in range(900, public_to + 1)] + list(extra_public)
    write_lines(tape / f"tape-{hg.hour_key(T - 3600)}.jsonl", rows)
    clk = {"ms": T * 1000 - 34_000}
    cap = fm.DailyCreditCap(cap_credits, tmp_path / "cap.json", clock=lambda: clk["ms"] / 1000)

    def to_rows(msg, recv_ms, commitment, pool_mints, counters):
        res = msg["params"]["result"]
        return [bd(res["context"]["slot"], res["value"]["signature"], t_recv=recv_ms)]

    def factory(mark, *, commitment, cap, on_rows, pool_mints):
        open_ms = (mark - 30) * 1000
        logs = [(open_ms + 180 + (s - 1000) * 400 + 50, logs_msg(f"P{s}" if s <= 1010 else f"W{s}", s, [])) for s in range(1001, 1046)]
        clock = Clock((mark - 60) * 1000)
        ws = FakeWS(script(mark, tip0=1000, extra=logs))
        w = fm.HeliusWindow(mark, "wss://helius.example.test/?api-key=" + FAKE_KEY, commitment=commitment, cap=cap, on_rows=on_rows, pool_mints=pool_mints,
                            connect=kw.pop("connect", None) or FakeConnect(ws), recv=make_recv(clock), now_ms=clock.now, sleep=clock.sleep, to_rows=to_rows)
        return w

    src = hg.LiveSource(tape_dir=tape, cap=cap, url="wss://x/?api-key=" + FAKE_KEY, start_hour_s=T - 3600, now_ms=lambda: clk["ms"],
                        sleep=lambda s: clk.__setitem__("ms", clk["ms"] + int(s * 1000)), window_factory=factory, spawn=spawn or hg.inline_spawn, require_ntp=False,
                        mirror_check=mirror, out_dir=tmp_path / "out", **kw)
    return src, clk


def start_and_read(src: hg.LiveSource, clk: dict) -> None:
    src.start()
    src._tick(clk["ms"] + 2_000)  # inside [T - 35 s, T + 10 s): the window starts and (inline) plays out
    src._read_public(clk["ms"])


def test_the_library_result_is_always_truthy_and_the_source_result_is_not():
    assert bool(fm.Completeness(False, "x")) is True  # why Certified exists: `if source.mark_complete(T, sd)` on the raw result would certify everything
    c = hg.certified(fm.Completeness(False, "x"))
    assert bool(c) is False and c.reason == "x" and bool(hg.certified(fm.Completeness(True, "ok"))) is True


def test_mark_complete_needs_the_seam_the_window_and_every_row_handed_to_the_host(tmp_path: Path):
    src, clk = make_source(tmp_path, public_to=1010)
    start_and_read(src, clk)
    assert src.merger.public_hw_slot() == 1010 and src._pending
    c = src.mark_complete(T, 1040)
    assert not c and c.reason == "rows_in_flight"  # accepted rows are still on their way to the host
    src._pending.clear()  # the host has consumed them
    c = src.mark_complete(T, 1040)
    assert bool(c) is True and c.complete and c.reason == "ok" and c.covered_from_slot == 1001 and c.ready_tip_slot == 1041
    assert src.failed(T) is None
    # a sd the window did not reach, and a sd before its coverage
    assert not src.mark_complete(T, 1200) and src.certs[T].reason == "tip_below_sd_plus_1"
    assert not src.mark_complete(T, 990) and src.certs[T].reason == "armed_late" and src.failed(T) == "armed_late"


def test_a_public_stream_that_lags_keeps_the_seam_open_and_catching_up_closes_it(tmp_path: Path):
    src, clk = make_source(tmp_path, public_to=990)
    start_and_read(src, clk)
    src._pending.clear()
    c = src.mark_complete(T, 1040)
    assert not c and c.reason == "seam_open" and src.public_hw_slot() == 990
    with open(tmp_path / "tape" / f"tape-{hg.hour_key(T - 3600)}.jsonl", "ab") as f:
        for s in range(991, 1012):
            f.write(json.dumps(bd(s, f"P{s}", t_recv=(T - 3600) * 1000 + 50_000 + s)).encode() + b"\n")
    src._read_public(clk["ms"])
    src._pending.clear()
    assert src.mark_complete(T, 1040) and src.public_hw_slot() == 1011


def test_a_public_gap_below_the_window_keeps_mark_complete_false_end_to_end(tmp_path: Path):
    g = gap("slot_jump", from_slot=995, to_slot=1005, t_recv_ms=(T - 3600) * 1000 + 99_000)
    src, clk = make_source(tmp_path, public_to=1012)
    # a silence closed record inside the stream: public rows to 995, a gap [996, 1004], then rows 1005..
    tape = tmp_path / "tape" / f"tape-{hg.hour_key(T - 3600)}.jsonl"
    rows = [bd(s, f"P{s}", t_recv=(T - 3600) * 1000 + (s - 900) * 100) for s in range(900, 996)] + [g] + [bd(s, f"P{s}", t_recv=(T - 3600) * 1000 + 99_000 + s)
                                                                                                          for s in range(1005, 1013)]
    write_lines(tape, rows)
    start_and_read(src, clk)
    src._pending.clear()
    c = src.mark_complete(T, 1040)
    assert not c and c.reason == "seam_open" and src.public_hw_slot() == 995  # [996, 1004] is missing from both sources


def test_failed_window_and_credit_cap_codes(tmp_path: Path):
    bad = FakeConnect(exc=OSError("boom " + FAKE_KEY))
    src, clk = make_source(tmp_path, connect=bad)
    start_and_read(src, clk)
    assert src.failed(T) == "connect_failed" and not src.mark_complete(T, 1040)
    assert FAKE_KEY not in json.dumps(src.report(), default=str)
    assert FAKE_KEY not in (tmp_path / "out" / "glue" / "events.jsonl").read_text()
    # credit cap reached: no window opens, exits are the host's, buys are held with a log-safe code
    src2, clk2 = make_source(tmp_path / "b", cap_credits=1.0) if (tmp_path / "b").mkdir() is None else (None, None)
    src2.cap.add(10**6)
    assert src2.buy_hold() == "credit_cap_reached" and hg.CODE_RE.fullmatch(src2.buy_hold())
    start_and_read(src2, clk2)
    assert src2.failed(T) == "cap_closed"
    src3, _ = make_source(tmp_path / "c") if (tmp_path / "c").mkdir() is None else (None, None)
    assert src3.buy_hold() is None


def test_window_stays_open_for_the_grace_after_complete_and_tip_slot_is_honest(tmp_path: Path):
    src, clk = make_source(tmp_path, complete_grace_s=3.0)
    start_and_read(src, clk)
    src._pending.clear()
    w = src._wins[T]
    calls = []
    w.window.complete = lambda: calls.append(clk["ms"])  # type: ignore[method-assign]
    assert src.mark_complete(T, 1040)
    t_cert = w.complete_at
    src._tick(t_cert + 2_999)
    assert calls == []
    src._tick(t_cert + 3_000)
    assert len(calls) == 1
    src._tick(t_cert + 9_000)
    assert len(calls) == 1  # once
    # tip_slot: the processed tip while the window is open; None for confirmed and for a closed window (a stale tip would understate the d_* latencies)
    assert src.tip_slot("confirmed") is None and src.tip_slot("processed") is None  # the inline window is over
    w.window.state.closed_ms = None
    assert src.tip_slot("processed") == w.window.state.tips[-1][1] and src.tip_slot() == w.window.state.tips[-1][1]


def test_iteration_yields_rows_then_idle_ticks_and_stops(tmp_path: Path):
    src, clk = make_source(tmp_path, public_to=1012, stop_when=lambda: clk["ms"] > T * 1000 + 12_000)
    items = list(src)
    rows = [i for i in items if i is not None]
    sigs = [r[0]["signature"] for r in rows]
    assert len(sigs) == len(set(sigs))  # the public copy and the window copy of the same transaction: one
    assert {f"W{s}" for s in range(1011, 1046)} <= set(sigs) and {f"P{s}" for s in range(900, 1013)} <= set(sigs)
    assert any(i is None for i in items)  # idle ticks so the host can act on time
    ev = [json.loads(x)["event"] for x in (tmp_path / "out" / "glue" / "events.jsonl").read_text().splitlines()]
    assert ev[0] == "start" and "window_start" in ev and "window_end" in ev and ev[-1] == "stop"
    assert (tmp_path / "out" / "glue" / "credit-days.jsonl").exists()


# ----------------------------------------------------------------------------------------------------------------------------------- the tape mirror
def test_start_refuses_when_the_mirror_is_not_fresh_and_passes_need_hours(tmp_path: Path):
    seen = []

    def check(need):
        seen.append(need)
        return "sync_stale"

    src, _ = make_source(tmp_path, mirror=check, mirror_need_hours=52)
    with pytest.raises(hg.MirrorUnhealthy, match="sync_stale"):
        src.start()
    assert seen == [52]
    ok, _ = make_source(tmp_path / "ok", mirror=lambda need: None) if (tmp_path / "ok").mkdir() is None else (None, None)
    ok.start()  # a fresh mirror starts
    boom, _ = make_source(tmp_path / "boom", mirror=lambda need: 1 / 0) if (tmp_path / "boom").mkdir() is None else (None, None)
    with pytest.raises(hg.MirrorUnhealthy, match="mirror_check_error"):  # a check that raises is a problem (fail closed)
        boom.start()


def test_a_mirror_problem_makes_every_mark_unhealthy_until_it_clears(tmp_path: Path):
    state = {"p": None}
    src, clk = make_source(tmp_path, mirror=lambda need: state["p"], mirror_ttl_s=5.0)
    start_and_read(src, clk)
    src._pending.clear()
    assert src.mark_complete(T, 1040) and src.failed(T) is None
    state["p"] = "sync_stale"
    clk["ms"] += 6_000  # past the cache
    c = src.mark_complete(T, 1040)
    assert not c and c.reason == "mirror:sync_stale"
    f = src.failed(T)
    assert f == "mirror_sync_stale" and hg.CODE_RE.fullmatch(f)  # ':' is not in the host's code alphabet
    # cached inside the ttl: still unhealthy even if the file already healed
    state["p"] = None
    assert not src.mark_complete(T, 1040)
    clk["ms"] += 6_000
    assert src.mark_complete(T, 1040) and src.failed(T) is None
    # a new mark is looked at once at its first call, regardless of the cache
    state["p"] = "status_missing"
    assert src.failed(T + 3600) == "mirror_status_missing" and src.stats["mirror_problem_status_missing"] >= 1
    assert src.report()["mirror_problem_now"] == "status_missing"


def test_default_mirror_check_resolves_tools_tape_mirror_lazily_and_fails_closed(tmp_path: Path, monkeypatch):
    check = hg.default_mirror_check(tmp_path)
    monkeypatch.setitem(sys.modules, "tools.tape_mirror", None)  # not importable (PR #1147 not merged)
    assert check(None) == "mirror_module_missing"
    fake = types.ModuleType("tools.tape_mirror")
    fake.mirror_problem = lambda dst, need_hours=None: f"seen:{Path(dst).name}:{need_hours}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools.tape_mirror", fake)
    assert check(52) == f"seen:{tmp_path.name}:52"


# ----------------------------------------------------------------------------------------------------------------------------------- the CLI
def args(tmp_path: Path, **kw) -> Namespace:
    (tmp_path / "tape").mkdir(exist_ok=True)
    key = tmp_path / "helius.env"
    key.write_text(f"HELIUS_API_KEY={FAKE_KEY}\n")
    d = dict(cmd="run", tape_dir=str(tmp_path / "tape"), key_file=str(key), declog_root=str(tmp_path / "declog"), out=str(tmp_path / "out"), commitment="processed",
             daily_credit_cap=50_000.0, trigger="certified", change_id="chg-1", catch_up_hours=0, universe_min_mbt=None, max_marks=None, mirror_check="off",
             mirror_need_hours=None)
    d.update(kw)
    return Namespace(**d)


SYNCED = lambda argv, t: (0, {"timedatectl": "yes\n"}.get(argv[0], "") if argv[1:2] == ["show"] else "")  # noqa: E731


def ntp_ok(argv, timeout):
    if argv[0] == "timedatectl" and argv[1] == "show":
        return 0, "yes\n"
    if argv[0] == "chronyc":
        return 0, "System time     : 0.000010000 seconds fast of NTP time\nLeap status     : Normal\n"
    return 127, ""


def ntp_no(argv, timeout):
    return (0, "no\n") if argv[:2] == ["timedatectl", "show"] else (127, "")


class FakeHost:
    calls: list = []

    class DryHost:
        def __init__(self, engine, log, source, **kw):
            FakeHost.calls.append(("init", engine, log, source, kw))

        def start(self):
            FakeHost.calls.append(("start",))
            return {}

        def run(self, max_marks=None):
            FakeHost.calls.append(("run", max_marks))
            return {"marks": 0}


def test_cli_refusals(tmp_path: Path, capsys):
    a = lambda **kw: args(tmp_path, **kw)  # noqa: E731
    ok_sha = lambda: "a" * 40  # noqa: E731
    for bad, why in ((a(daily_credit_cap=100_001.0), "100000"), (a(daily_credit_cap=0.0), "positive"), (a(daily_credit_cap=float("nan")), "positive"),
                     (a(tape_dir=str(tmp_path / "nope")), "tape-dir"), (a(trigger="guard"), "certified"), (a(change_id="bad id"), "change-id"),
                     (a(key_file=str(tmp_path / "nokey")), "key-file"), (a(catch_up_hours=100), "catch-up")):
        with pytest.raises(hg.Refused, match=why):
            hg.cmd_run(bad, host_module=FakeHost, ntp_runner=ntp_ok, repo_sha=ok_sha)
    assert hg.cmd_run  # 50,000 and 100,000 are fine; checked by the happy path below and here:
    hg.check_args(a(daily_credit_cap=100_000.0))
    # main() turns a refusal into exit code 2 and never prints the key
    rc = hg.main(["run", "--tape-dir", str(tmp_path / "tape"), "--key-file", str(tmp_path / "helius.env"), "--declog-root", str(tmp_path / "d"), "--out",
                  str(tmp_path / "o"), "--daily-credit-cap", "100001", "--change-id", "x"])
    cap = capsys.readouterr()
    assert rc == 2 and "REFUSED" in cap.err and FAKE_KEY not in cap.out + cap.err


def test_cli_refuses_an_unsynced_clock_and_a_dirty_tree_and_does_not_start_the_host(tmp_path: Path):
    pytest.importorskip("duckdb")
    from tools import r4b_live as lv

    FakeHost.calls = []
    with pytest.raises(fm.ClockNotSynced):
        hg.cmd_run(args(tmp_path), host_module=FakeHost, ntp_runner=ntp_no, repo_sha=lambda: "a" * 40)

    def dirty():
        raise lv.LiveRefused("modified tracked path(s)")

    with pytest.raises(lv.LiveRefused):
        hg.cmd_run(args(tmp_path), host_module=FakeHost, ntp_runner=ntp_ok, repo_sha=dirty)
    assert FakeHost.calls == []
    # the real check: a git repo with a modified tracked file
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    (repo / "f").write_text("1")
    subprocess.run(["git", "-C", str(repo), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    assert len(lv.repo_head_sha(repo)) == 40
    (repo / "f").write_text("2")
    with pytest.raises(lv.LiveRefused, match="modified"):
        lv.repo_head_sha(repo)


def test_cli_refuses_an_unfresh_mirror_before_the_host_writes_its_start_row(tmp_path: Path):
    pytest.importorskip("duckdb")
    FakeHost.calls = []
    with pytest.raises(hg.MirrorUnhealthy, match="status_stale"):
        hg.cmd_run(args(tmp_path, mirror_check="auto", catch_up_hours=52), host_module=FakeHost, ntp_runner=ntp_ok, repo_sha=lambda: "b" * 40,
                   mirror_check=lambda need: "status_stale" if need == 52 else None)
    assert FakeHost.calls == []  # DryHost was never even built, so no `start` row
    assert hg.main is not None


def test_cli_runs_the_host_with_the_warm_up_engine_and_the_live_source(tmp_path: Path, capsys):
    pytest.importorskip("duckdb")
    FakeHost.calls = []
    a = args(tmp_path, max_marks=3, mirror_check="auto", catch_up_hours=52)
    assert hg.cmd_run(a, host_module=FakeHost, ntp_runner=ntp_ok, repo_sha=lambda: "c" * 40, mirror_check=lambda need: None) == 0
    kinds = [c[0] for c in FakeHost.calls]
    assert kinds == ["init", "start", "run"] and FakeHost.calls[2] == ("run", 3)
    _, engine, log, source, kw = FakeHost.calls[0]
    assert engine.warmup_s == 52 * 3600 and isinstance(source, hg.LiveSource) and kw["frozen_sha"] == "c" * 40 and kw["ntp_offset_ms"] == pytest.approx(0.01)
    assert source.mirror_need_hours == 52 and source.cap.cap == 50_000.0 and source.first_mark is not None
    run = json.loads((tmp_path / "out" / "glue" / "run.json").read_text())
    assert run["change_id"] == "chg-1" and run["trigger"] == "certified" and run["live_built"] is False and run["frozen_sha"] == "c" * 40
    text = (tmp_path / "out" / "glue" / "run.json").read_text() + (tmp_path / "out" / "glue" / "run-summary.json").read_text() + capsys.readouterr().out
    assert FAKE_KEY not in text
    assert json.loads((tmp_path / "out" / "glue" / "run-summary.json").read_text())["host_counts"] == {"marks": 0}


def test_script_form_with_isolated_mode_works_and_the_module_has_no_send_path(tmp_path: Path):
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "r4b_host_glue.py"), "run", "--help"], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and "--daily-credit-cap" in r.stdout and "--change-id" in r.stdout
    src = (ROOT / "tools" / "r4b_host_glue.py").read_text()
    code_only = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for word in ("sendTransaction", "Keypair", "solders", "private_key", "wallet.json", "api_key=", "print(url", "print(self._url"):
        assert word not in code_only, word
    assert hg.LIVE_BUILT is False
