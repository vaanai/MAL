"""Tests for tools/arena_tip_adapter.py (QP-RULING-1101-hv4-reads 4.1 (B) item 2): row mapping, gaps as data_loss, skipped slots are not loss, slot order and tx_index order
keys, refusals, fail-closed health, the hour rollover with the REAL tools.fast_tip_follower.TipFollower, the feed-delay helper, and mutation checks.
SYNTHETIC rows, tmp dirs and a fake RPC only: no real tip file, arena tape, mirror, ledger or network is touched.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_adapter.py -q
"""
from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import r4b_tip_reader as tr  # noqa: E402
from tools.arena_paper import MIN_FREE_PCT_FLOOR, ArenaEngine, StackConfig  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.test_fast_tip_follower import Rpc, _follower, _seed  # noqa: E402,F401
from tools.test_r4b_tip_reader import SetClock, put, write_state  # noqa: E402

WSOL = "So11111111111111111111111111111111111111112"
B = int(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).timestamp())
H05, H06 = "2026-11-01T05", "2026-11-01T06"
T0 = B * 1000


def wt(slot: int, i: int = 0, *, venue: str = "pump_bonding", t: int | None = None, tx: int | None = None, **kw) -> dict:
    """A tip-format walker trade row."""
    r = {"venue": venue, "mint": f"M{slot % 3}", "trader": "T", "side": "buy" if (slot + i) % 2 else "sell", "sol_lamports": 10**9, "token_raw": 10**6, "quote_reserve": 30 * 10**9,
         "base_reserve": 10**15, "pool": None, "slot": slot, "signature": f"s{slot}x{i}", "event_index": 0, "tx_index": i if tx is None else tx, "block_time": B + slot // 2,
         "event_ts": B + slot // 2, "t_recv_ms": T0 + slot * 400 if t is None else t, "source": "tip", "virtual_quote_reserve": None, "quote_is_wsol": True, "price_sol": 1e-9, "v": 1}
    if venue == "pumpswap":
        r.update({"pool": f"P{slot % 3}", "quote_mint": WSOL, "virtual_quote_reserves": 17_600_000_000, "mint": f"M{slot % 3}", "base_reserve": 206 * 10**12, "quote_reserve": 85 * 10**9})
    r.update(kw)
    return r


def mk_engine(tmp: Path, **cfg) -> ArenaEngine:
    eng = ArenaEngine(tmp / "ledger", tmp / "bots.json", StackConfig(feed_delay_slots=2, **cfg), now_fn=lambda: 0, code_state_fn=lambda: ("0" * 40, False), min_free_pct=MIN_FREE_PCT_FLOOR)
    eng.now_fn = lambda: eng.hw_ms or 0
    return eng


# ------------------------------------------------------------------------------------------------------------------------------------ row mapping
def arena_feed_consts() -> dict:
    """NAME_MAX, SYMBOL_MAX, TRADE_DROP and VENUE_PROG read from tools/arena_feed.py's source (importing it needs certifi, which the test venv lacks)."""
    src = (Path(ta.__file__).parent / "arena_feed.py").read_text()
    m = re.search(r"^NAME_MAX, SYMBOL_MAX = (\d+), (\d+)", src, re.M)
    out = {"NAME_MAX": int(m.group(1)), "SYMBOL_MAX": int(m.group(2))}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("TRADE_DROP", "VENUE_PROG"):
            v = node.value
            out[node.targets[0].id] = ast.literal_eval(v.args[0]) if isinstance(v, ast.Call) else ast.literal_eval(v)
    return out


def test_constants_follow_the_arena_feed():
    c = arena_feed_consts()
    assert (ta.NAME_MAX, ta.SYMBOL_MAX) == (c["NAME_MAX"], c["SYMBOL_MAX"]) and set(c["TRADE_DROP"]) <= ta.TRADE_DROP and ta.VENUE_PROG == c["VENUE_PROG"]
    assert {"source", "virtual_quote_reserve"} <= ta.TRADE_DROP and "tx_index" not in ta.TRADE_DROP and "block_time" not in ta.TRADE_DROP


def test_a_pumpswap_trade_maps_to_the_tape_shape_with_tx_index_and_event_v():
    r = ta.trade_row(wt(10, 2, venue="pumpswap", tx=7, zero_sol=True))
    assert r["type"] == "trade" and r["prog"] == "pumpswap" and r["v"] == 1 and r["venue"] == "pumpswap"
    assert (r["slot"], r["signature"], r["event_index"], r["tx_index"], r["block_time"]) == (10, "s10x2", 0, 7, B + 5)
    assert r["t_recv_ms"] == T0 + 4000 and r["virtual_quote_reserves"] == 17_600_000_000 and r["pool"] == "P1" and r["quote_mint"] == WSOL and r["zero_sol"] is True
    for gone in ("source", "virtual_quote_reserve", "price_sol", "quote_is_wsol", "t_recv"):
        assert gone not in r


def test_a_bonding_trade_maps_to_pumpfun_and_keeps_its_pool_null():
    r = ta.trade_row(wt(11))
    assert r["prog"] == "pumpfun" and r["pool"] is None and r["mint"] == "M2" and r["tx_index"] == 0 and r["event_ts"] == B + 5


def test_create_complete_and_migration_rows():
    c = ta.create_row({"type": "create", "mint": "M", "trader": "u", "creator": "c", "event_ts": 5, "quote_reserve": 1, "base_reserve": 2, "real_token_reserves": 3, "token_raw": 4,
                       "quote_mint": WSOL, "is_mayhem_mode": False, "name": "n" * 50, "symbol": "s" * 30, "slot": 9, "signature": "sg", "event_index": 0, "tx_index": 3, "block_time": 6,
                       "t_recv_ms": 77, "source": "tip", "feed": "x"})
    assert c["type"] == "create" and c["prog"] == "pumpfun" and len(c["name"]) == 32 and len(c["symbol"]) == 16 and c["tx_index"] == 3 and "source" not in c and c["t_recv_ms"] == 77
    (cm,) = ta.migration_rows({"type": "complete", "mint": "M", "trader": "u", "bonding_curve": "bc", "event_ts": 5, "quote_mint": WSOL, "slot": 9, "signature": "sg", "event_index": 0,
                               "tx_index": 0, "block_time": 6, "t_recv_ms": 77})
    assert cm["type"] == "complete" and cm["prog"] == "pumpfun" and cm["bonding_curve"] == "bc"
    (pc,) = ta.migration_rows({"type": "migration", "mint": "M", "pool": "POOL", "quote_mint": WSOL, "slot": 9, "signature": "sg", "event_index": 1, "tx_index": 0, "block_time": 6,
                               "t_recv_ms": 77, "trader": "u", "sol_lamports": 5})
    assert pc["type"] == "pool_create" and pc["prog"] == "pumpswap" and (pc["pool"], pc["mint"], pc["quote_mint"]) == ("POOL", "M", WSOL) and "sol_lamports" not in pc
    with pytest.raises(ta.BadRow):
        ta.migration_rows({"type": "other", "slot": 1, "t_recv_ms": 1})


def test_the_engine_and_the_state_take_every_row_type(tmp_path):
    ad = ta.ArenaTipAdapter()
    items = [("migration", {"type": "migration", "mint": "M0", "pool": "P0", "quote_mint": WSOL, "slot": 9, "signature": "m", "event_index": 0, "tx_index": 0, "block_time": B, "t_recv_ms": T0}),
             ("create", {"type": "create", "mint": "M1", "creator": "c", "slot": 9, "signature": "c", "event_index": 0, "tx_index": 1, "block_time": B, "t_recv_ms": T0, "quote_reserve": 30 * 10**9,
                         "base_reserve": 10**15}),
             ("trade", wt(10, 0, venue="pumpswap")), ("trade", wt(10, 1)), ("trade", wt(11, 0, venue="pumpswap"))]
    rows = ad.feed(items)
    assert [r["type"] for r in rows] == ["pool_create", "create", "trade", "trade", "trade"]
    eng, st = mk_engine(tmp_path), ArenaState(tmp_path / "tape")
    for r in rows:
        eng.on_row(r)
        st.apply(r)
    assert eng.counters.get("rows_skipped", 0) == 0 and st.counters["rows"] == 5 and st.pool("P1") is not None and st.counters["trade_without_key"] == 0


def test_every_print_gets_the_tx_index_order_key_form():
    assert order_key_scenario() == []  # form 0 (slot, 0, tx_index, event_index, signature) on every print, never the receive-order form 1


# ------------------------------------------------------------------------------------------------------------------------------------ gaps, skips, bad rows
def gap_scenario(tmp_path: Path) -> list[str]:
    """Trades 10 and 11; a backlog_jump over 12..40; slots 41 skipped; trades 42 and 43. Returns the violations."""
    bad = []
    g = tr.TipGap(12, 40, "backlog_jump", T0 + 12 * 400 + 50, 29)
    items = [("trade", wt(10)), ("trade", wt(11)), ("gap", g), ("skip", 41), ("trade", wt(42)), ("trade", wt(43, tx=1))]
    rows = ta.ArenaTipAdapter().feed(items)
    kinds = [(r["type"], r["slot"]) for r in rows]
    if [k[0] for k in kinds] != ["trade", "trade", "gap", "trade", "trade"]:
        return [f"row order {kinds}"]
    gp = rows[2]
    want = {"prog": "all", "kind": "tip_gap_backlog_jump", "data_loss": True, "from_slot": 12, "to_slot": 40, "missed_slots": 29, "reason": "backlog_jump"}
    bad += [f"gap {k}={gp.get(k)}" for k, v in want.items() if gp.get(k) != v]
    if not (gp["from_ms"] == rows[1]["t_recv_ms"] and rows[1]["t_recv_ms"] <= gp["to_ms"] <= rows[3]["t_recv_ms"] and gp["t_recv_ms"] == gp["to_ms"]):
        bad.append(f"gap span {gp['from_ms']}..{gp['to_ms']} vs rows {rows[1]['t_recv_ms']}..{rows[3]['t_recv_ms']}")
    if [r["slot"] for r in rows] != sorted(r["slot"] for r in rows):
        bad.append("slot order broken")
    eng = mk_engine(tmp_path, gap_slots=None, gap_silence_ms=None)  # the engine's own jump detection off: only the adapter's gap row counts
    for r in rows:
        eng.on_row(r)
    if eng.counters.get("gap_data_loss") != 1 or len(eng.gaps) != 1 or eng.gaps[0].prog != "all":
        bad.append(f"engine gaps {eng.counters} {eng.gaps}")
    elif not (eng._gap_hit("pumpswap", (T0, T0 + 10**9, 0, 11)) and eng._gap_hit("pump_bonding", (0, 0, 20, 30)) and not eng._gap_hit("pumpswap", (0, 5, 0, 5))):
        bad.append("engine _gap_hit does not see the range")
    return bad


def test_a_backlog_jump_is_one_data_loss_gap_row_in_slot_position_and_the_engine_keeps_it(tmp_path):
    assert gap_scenario(tmp_path) == []


def skip_scenario(tmp_path: Path) -> list[str]:
    bad = []
    ad = ta.ArenaTipAdapter()
    rows = ad.feed([("trade", wt(10)), ("skip", 11), ("skip", 12), ("trade", wt(13))])
    if [r["type"] for r in rows] != ["trade", "trade"] or ad.stats["skips"] != 2 or ad.stats["gap_rows"]:
        bad.append(f"skip became a row or a gap: {[r['type'] for r in rows]} {dict(ad.stats)}")
    eng = mk_engine(tmp_path, gap_slots=None, gap_silence_ms=None)
    for r in rows:
        eng.on_row(r)
    if eng.gaps or "gap_data_loss" in eng.counters:
        bad.append("the engine holds a gap for a skipped slot")
    return bad


def test_a_skipped_slot_is_not_data_loss(tmp_path):
    assert skip_scenario(tmp_path) == []


def order_key_scenario() -> list[str]:
    rows = ta.ArenaTipAdapter().feed([("trade", wt(10, 3, tx=5, event_index=1)), ("trade", wt(10, 0, tx=2))])
    keys = [ArenaEngine.order_key(r, r["slot"], r["t_recv_ms"]) for r in rows]
    return [] if keys == [(10, 0, 5, 1, "s10x3"), (10, 0, 2, 0, "s10x0")] else [f"order keys {keys}"]


@pytest.mark.parametrize("reason", ["backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped", "tip_bad_line", "hour_file_missing", "unparsable"])
def test_every_reader_gap_reason_becomes_a_data_loss_row(reason):
    rows = ta.ArenaTipAdapter().feed([("trade", wt(10)), ("gap", tr.TipGap(11, 11, reason, T0 + 4400, 1)), ("trade", wt(12))])
    g = [r for r in rows if r["type"] == "gap"]
    assert len(g) == 1 and g[0]["data_loss"] is True and g[0]["kind"] == tr.TipGap(11, 11, reason, None).code and g[0]["prog"] == "all" and g[0]["missed_slots"] == 1


def test_a_gap_beyond_the_released_slots_waits_and_then_goes_out():
    ad = ta.ArenaTipAdapter()
    g = tr.TipGap(100, 120, "backlog_jump", T0 + 99_000, 21)
    assert [r["type"] for r in ad.feed([("trade", wt(10)), ("gap", g)], released_D=50)] == ["trade"] and ad.pending_gaps() == [g]
    out = ad.feed([], released_D=99)  # slots through 99 released: the gap starts at 100
    assert [r["type"] for r in out] == ["gap"] and not ad.pending_gaps() and out[0]["slot"] == 10


def test_a_late_gap_for_an_older_slot_goes_out_before_the_next_row_with_its_time_between_the_rows():
    ad = ta.ArenaTipAdapter()
    ad.feed([("trade", wt(10, t=T0 + 1000)), ("trade", wt(30, t=T0 + 9000))])
    out = ad.feed([("gap", tr.TipGap(12, 12, "skip_contradicted", T0 + 20_000, 1)), ("trade", wt(31, t=T0 + 9400))])
    assert [r["type"] for r in out] == ["gap", "trade"] and out[0]["from_ms"] == T0 + 9000 and out[0]["to_ms"] == T0 + 9400 and out[0]["slot"] == 30  # clamped to the next row; slot never goes back


def test_a_gap_with_no_instant_anywhere_waits_for_the_first_row():
    ad = ta.ArenaTipAdapter()
    assert ad.feed([("gap", tr.TipGap(5, 9, "tip_bad_line", None, None))], released_D=100) == []
    out = ad.feed([("trade", wt(10, t=T0 + 777))])
    assert [r["type"] for r in out] == ["gap", "trade"] and out[0]["to_ms"] == T0 + 777


def bad_row_scenario() -> list[str]:
    bad = []
    resolved = wt(20, 1, venue="pumpswap")
    del resolved["tx_index"]  # what resolve_unresolved gives: a row that came back through an account lookup has no tx_index
    ad = ta.ArenaTipAdapter()
    rows = ad.feed([("trade", wt(19)), ("trade", resolved), ("trade", wt(20, 2)), ("trade", wt(21))])
    if [r["type"] for r in rows] != ["trade", "gap", "trade", "trade"]:
        return [f"order {[r['type'] for r in rows]}"]
    g = rows[1]
    if not (g["kind"] == "tip_gap_bad_row_tx_index" and g["data_loss"] and g["from_slot"] == g["to_slot"] == 20 and ad.stats["rows_dropped_tx_index"] == 1):
        bad.append(f"gap {g}")
    bad += [f"row without tx_index passed: {r['signature']}" for r in rows if r["type"] == "trade" and not isinstance(r.get("tx_index"), int)]
    return bad


def test_a_row_without_tx_index_is_dropped_and_marked_never_passed_on():
    assert bad_row_scenario() == []


@pytest.mark.parametrize("patch", [{"venue": "other"}, {"side": "hold"}, {"sol_lamports": "1"}, {"signature": ""}, {"slot": True}])
def test_other_unusable_rows_are_dropped_and_marked(patch):
    rows = ta.ArenaTipAdapter().feed([("trade", wt(10)), ("trade", {**wt(11), **patch}), ("trade", wt(12))])
    types = [r["type"] for r in rows]
    assert types.count("trade") == 2 and types.count("gap") == 1


# ------------------------------------------------------------------------------------------------------------------------------------ mutations of the adapter
@pytest.mark.parametrize("name", ["no_flush", "not_data_loss", "skip_is_gap", "gap_at_end", "tx_index_stripped", "bad_row_passes"])
def test_mutations_are_caught(tmp_path, monkeypatch, name):
    if name == "no_flush":
        monkeypatch.setattr(ta.ArenaTipAdapter, "_flush", lambda self, out, upto_slot, next_t: None)
    elif name == "not_data_loss":
        orig = ta.gap_row
        monkeypatch.setattr(ta, "gap_row", lambda *a, **k: {**orig(*a, **k), "data_loss": False})
    elif name == "skip_is_gap":
        orig_feed = ta.ArenaTipAdapter.feed
        monkeypatch.setattr(ta.ArenaTipAdapter, "feed", lambda self, items, released_D=None: orig_feed(
            self, [("gap", tr.TipGap(x, x, "skipped", None, 1)) if k == "skip" else (k, x) for k, x in items], released_D))
    elif name == "gap_at_end":
        orig_flush = ta.ArenaTipAdapter._flush
        monkeypatch.setattr(ta.ArenaTipAdapter, "_flush", lambda self, out, upto_slot, next_t: orig_flush(self, out, upto_slot, next_t) if upto_slot > 1000 else None)
    elif name == "tx_index_stripped":
        orig_t = ta.trade_row
        monkeypatch.setattr(ta, "trade_row", lambda w: {k: v for k, v in orig_t(w).items() if k != "tx_index"})
    elif name == "bad_row_passes":
        orig_t = ta.trade_row
        monkeypatch.setattr(ta, "trade_row", lambda w: orig_t({**w, "tx_index": w.get("tx_index", 0)}))
    if name == "skip_is_gap":
        assert skip_scenario(tmp_path) != []
    elif name == "tx_index_stripped":
        assert order_key_scenario() != []
    elif name == "bad_row_passes":
        assert bad_row_scenario() != []
    else:
        assert gap_scenario(tmp_path) != []


# ------------------------------------------------------------------------------------------------------------------------------------ refusals
def test_the_archive_and_any_copy_of_it_are_refused(tmp_path):
    for d in (tr.ARCHIVE_DIR, tr.ARCHIVE_DIR + "/2026-10-20", str(tmp_path / "tip-tape-archive")):
        with pytest.raises(tr.TipRefused):
            ta.ArenaTipSource.open(d, tmp_path, start_ms=T0)


def test_hours_before_the_floor_and_a_long_look_back_are_refused(tmp_path):
    early = int(datetime(2026, 10, 16, 3, tzinfo=timezone.utc).timestamp()) * 1000
    with pytest.raises(tr.TipRefused):
        ta.ArenaTipSource.open(tmp_path, tmp_path, start_ms=early, catch_up_hours=5)  # first hour 10-15T22
    with pytest.raises(tr.TipRefused):
        ta.ArenaTipSource.open(tmp_path, tmp_path, start_ms=T0, catch_up_hours=54)
    src = ta.ArenaTipSource.open(tmp_path, tmp_path, start_ms=T0, catch_up_hours=53)
    assert tr.hour_key(src.reader.h0) == "2026-10-30T01"
    for name in ("trades-2026-10-30T00.jsonl", "checkpoint.json", "status.json", "../x", "trades-2026-11-01T05.jsonl.zst"):
        with pytest.raises(tr.TipRefused):
            src.reader.files.path(name)


def test_the_production_floor_cannot_be_lowered_on_the_sealed_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "realpath", lambda p: os.fspath(p))
    with pytest.raises(tr.TipRefused):
        tr.TipReader(tr.DEFAULT_TIP_DIR, tr.DEFAULT_STATE_DIR, start_ms=T0, min_hour="2026-08-01T00", lookback_h=None)
    assert ta.ArenaTipSource.open(tr.DEFAULT_TIP_DIR, tr.DEFAULT_STATE_DIR, start_ms=T0).reader.floor == "2026-10-30T01"


def healthy(state: Path, now: int, **kw) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "status.json").write_text(json.dumps({"ts_ms": now, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True, **kw}))


class FakeReader:
    def __init__(self, items=()):
        self.items, self.problem, self.released_D, self.caught_up, self.polls = list(items), None, None, True, 0

    def start_problem(self):
        return None

    def poll(self, limit=0):
        self.polls += 1
        return self.items


@pytest.mark.parametrize("kw,code", [({"ts_ms": 1}, "status_stale"), ({"decoder_blobs_pinned": False}, "decoder_blobs_not_pinned"), ({"trade_event_v": False}, "event_v_off"),
                                     ({"decoder_blobs": {"observe/trade_decode.py": "0" * 40}}, "decoder_blobs_mismatch")])
def test_an_unhealthy_tip_fails_closed_at_start_and_at_every_poll(tmp_path, kw, code):
    now = 1_800_000_000_000
    healthy(tmp_path / "a", kw.pop("ts_ms", now), **kw)
    with pytest.raises(ta.TipUnhealthy, match=code):
        ta.ArenaTipSource(FakeReader(), tmp_path / "a", now_ms=lambda: now, io_hook=lambda: True).start()
    healthy(tmp_path / "b", now)
    rd = FakeReader([("trade", wt(10))])
    clock = [now]
    src = ta.ArenaTipSource(rd, tmp_path / "b", now_ms=lambda: clock[0], io_hook=lambda: True)
    src.start()
    assert [r["type"] for r in src.poll()] == ["trade"] and src.problem is None
    clock[0] += 60_000 if code == "status_stale" else 0
    if code != "status_stale":
        healthy(tmp_path / "b", clock[0], **kw)
    assert src.poll() == [] and src.problem == f"tip:{code}" and rd.polls == 1  # no row of an unhealthy tip side is read


def test_start_refuses_without_idle_io_and_without_the_first_history_hour(tmp_path):
    healthy(tmp_path, 5)
    with pytest.raises(ta.TipUnhealthy, match="ionice"):
        ta.ArenaTipSource(FakeReader(), tmp_path, now_ms=lambda: 5, io_hook=lambda: False).start()
    ta.ArenaTipSource(FakeReader(), tmp_path, now_ms=lambda: 5, io_hook=lambda: False, require_io_idle=False).start()
    rd = FakeReader()
    rd.start_problem = lambda: "history_start_missing"
    with pytest.raises(ta.TipUnhealthy, match="history_start_missing"):
        ta.ArenaTipSource(rd, tmp_path, now_ms=lambda: 5, io_hook=lambda: True).start()
    with pytest.raises(ta.TipUnhealthy, match="start"):
        ta.ArenaTipSource(FakeReader(), tmp_path, now_ms=lambda: 5).poll()


def test_the_source_never_writes_to_the_tip_dir(tmp_path):
    out = tmp_path / "out"
    put(out, f"trades-{H05}.jsonl", [wt(s, t=(B - 600) * 1000 + s) for s in range(1, 6)])
    put(out, "gaps.jsonl", [{"kind": "gap", "reason": "backlog_jump", "from_slot": 3, "to_slot": 3, "slots": 1, "t_recv_ms": (B - 590) * 1000}])
    write_state(tmp_path / "state", last_done=5, last_t_recv_ms=(B - 500) * 1000)
    healthy(tmp_path / "state", T0)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in out.iterdir()}
    src = ta.ArenaTipSource(tr.TipReader(out, tmp_path / "state", start_ms=(B - 600) * 1000, catch_up_hours=0), tmp_path / "state", now_ms=lambda: T0, io_hook=lambda: True)
    src.start()
    rows = src.poll()
    assert [r["type"] for r in rows] == ["trade", "trade", "gap", "trade", "trade", "trade"] and src.caught_up and src.released_D == 5
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in out.iterdir()} and sorted(p.name for p in out.iterdir()) == sorted(before)


# ------------------------------------------------------------------------------------------------------------------------------------ the real follower, across the hour mark
def drive(tmp_path: Path, *, mid_polls: bool = True, n: int = 14, jump: bool = True) -> list[str]:
    """The REAL TipFollower across the hour mark B (slots 100..: the first land in hour 05, the rest in hour 06) with a skipped slot and a backlog jump. An ArenaTipSource polls after
    every commit and, with mid_polls, after each trades append but BEFORE the checkpoint moves. Violations: a row of a slot above the checkpoint, a missing row, rows out of slot
    order, a trade without tx_index, a skipped slot that became a gap, a backlog jump without its gap row in slot position."""
    clk = SetClock(B * 1000 - 3000)
    f = _follower(tmp_path, Rpc(tip=0, skipped={103}), clock=clk)
    _seed(f, 99)
    healthy(tmp_path / "state", clk.ms)
    f.precreate(clk.ms)  # the follower touches this hour's files (and the next hour's from :59:55): the first history hour exists
    src = ta.ArenaTipSource(tr.TipReader(tmp_path / "out", tmp_path / "state", start_ms=(B - 3600 + 600) * 1000, catch_up_hours=0), tmp_path / "state", now_ms=lambda: clk.ms,
                            io_hook=lambda: True)
    src.start()
    bad: list[str] = []
    got: list[dict] = []
    st = {"D": 99}

    def poll(tag: str) -> None:
        rows = src.poll()
        got.extend(rows)
        over = [r["slot"] for r in rows if r["type"] == "trade" and r["slot"] > st["D"]]
        if over:
            bad.append(f"{tag}: slots {over} beyond the checkpoint {st['D']}")

    orig_append = f.writer.append

    def append(kind, hour, rows):
        orig_append(kind, hour, rows)
        if mid_polls and kind == "trades" and rows:
            healthy(tmp_path / "state", clk.ms)
            poll(f"mid-write of {rows[0]['slot']}")

    f.writer.append = append
    for i, slot in enumerate(range(100, 100 + n)):
        clk.ms = B * 1000 - 3000 + i * 500
        healthy(tmp_path / "state", clk.ms)
        if jump and slot == 108:
            f._backlog_jump(112)  # slots 108..111 lost; the follower resumes at the tip
            f.last_done = 111
            continue
        if jump and slot in (109, 110, 111):
            continue
        f.process_slot(slot)
        st["D"] = f.last_done
        poll(f"after commit {slot}")
    truth = sorted(r["slot"] for p in (tmp_path / "out").glob("trades-*.jsonl") for r in map(json.loads, p.read_text().splitlines()) if r["slot"] <= f.last_done)
    trades = [r["slot"] for r in got if r["type"] == "trade"]
    if trades != truth:
        bad.append(f"trades emitted {sorted(set(truth) - set(trades))} missing, extra {sorted(set(trades) - set(truth))}")
    if [r["slot"] for r in got] != sorted(r["slot"] for r in got):
        bad.append("rows out of slot order")
    bad += [f"trade {r['signature']} without tx_index" for r in got if r["type"] == "trade" and not isinstance(r.get("tx_index"), int)]
    gaps = [r for r in got if r["type"] == "gap"]
    if jump:
        if not (len(gaps) == 1 and (gaps[0]["from_slot"], gaps[0]["to_slot"]) == (108, 111) and gaps[0]["data_loss"] and gaps[0]["kind"] == "tip_gap_backlog_jump"):
            bad.append(f"backlog gap rows {gaps}")
        else:
            i = got.index(gaps[0])
            if any(r["slot"] >= 108 for r in got[:i] if r["type"] == "trade"):
                bad.append("gap row after a row of slot >= its from_slot")
    if any("skip" in r["kind"] for r in gaps):
        bad.append("a skipped slot became a gap")
    if {p.name for p in (tmp_path / "out").glob("trades-*.jsonl")} != {f"trades-{H05}.jsonl", f"trades-{H06}.jsonl"}:
        bad.append("the run did not cross the hour")
    return bad


def test_the_real_follower_across_the_hour_mark_with_a_skip_and_a_backlog_jump(tmp_path):
    assert drive(tmp_path) == []


def test_the_protocol_holds_without_mid_write_polls(tmp_path):
    assert drive(tmp_path, mid_polls=False) == []


def test_the_real_follower_without_a_jump_gives_no_gap_rows(tmp_path):
    assert drive(tmp_path, jump=False) == []


def test_mutation_hour_rollover_tail_off_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(tr.TipReader, "_last_hour", lambda self, last_t_ms: self.h)
    bad = drive(tmp_path)
    assert bad and any("missing" in b for b in bad)


def test_mutation_releasing_rows_beyond_D_is_caught(tmp_path, monkeypatch):
    orig = tr.TipReader._merge
    monkeypatch.setattr(tr.TipReader, "_merge", lambda self, cur, D, budget, out: orig(self, cur, 10**18, budget, out))
    assert any("beyond the checkpoint" in b for b in drive(tmp_path))


# ------------------------------------------------------------------------------------------------------------------------------------ feed delay
def test_the_followers_real_status_has_the_lag_fields_the_delay_helper_reads(tmp_path):
    f = _follower(tmp_path, Rpc(tip=0), clock=SetClock(T0))
    assert {"lag_ms", "block_lag_ms_p50", "block_lag_ms_p90", "lag_slots", "ts_ms"} <= set(f.status())
    assert {"last_done", "last_t_recv_ms", "gaps"} <= set(json.loads(f.ckpt_path.read_text())) if f.ckpt_path.exists() else True


def test_feed_delay_from_samples_is_the_p90_in_whole_slots(tmp_path):
    state = tmp_path / "s"
    state.mkdir()
    (state / "status.json").write_text(json.dumps({"ts_ms": 5, "lag_ms": 1700, "block_lag_ms_p50": 1784, "block_lag_ms_p90": 2149, "lag_slots": 2, "backlog_jumps": 0}))
    (state / "checkpoint.json").write_text(json.dumps({"last_done": 9, "last_t_recv_ms": 4, "gaps": 0, "skipped": 0, "writing": None}))
    s = ta.read_lag_sample(state)
    assert s["block_lag_ms_p90"] == 2149 and s["last_done"] == 9 and ta.read_lag_sample(tmp_path / "none") is None
    samples = [{"block_lag_ms_p90": 1000 + 10 * i} for i in range(200)]  # 1000..2990 ms
    r = ta.feed_delay_slots(samples, 0.4)
    assert r["n"] == 200 and r["p90_ms"] == 2790 and r["d"] == 7 and r["d_median"] == 5 and r["max_ms"] == 2990
    assert ta.feed_delay_slots([{"block_lag_ms_p90": 2000}] * 100, 0.4)["d"] == 5
    with pytest.raises(ValueError):
        ta.feed_delay_slots(samples[:10], 0.4)
    with pytest.raises(ValueError):
        ta.feed_delay_slots(samples, 0.0)
