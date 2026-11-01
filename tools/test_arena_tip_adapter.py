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
from tools import arena_tip_reader as tr  # noqa: E402
from tools.arena_paper import MIN_FREE_PCT_FLOOR, ArenaEngine, StackConfig  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.test_fast_tip_follower import Rpc, _follower, _seed  # noqa: E402,F401


class SetClock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def write_state(state: Path, **kw) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "checkpoint.json").write_text(json.dumps({"last_done": None, "last_t_recv_ms": 0, "gaps": 0, "skipped": 0, "writing": None, **kw}))


def put(d: Path, name: str, rows: list, mode: str = "w") -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, mode) as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r, separators=(",", ":"))) + "\n")

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
    assert len(g) == 1 and g[0]["data_loss"] is True and g[0]["kind"] == ta.gap_kind(reason) == f"tip_gap_{reason}" and g[0]["prog"] == ("pumpswap" if reason == "unresolved_dropped" else "all") and g[0]["missed_slots"] == 1


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


def resolved_row(slot: int, i: int = 1) -> dict:
    """What a PumpSwap print that came back through resolve_unresolved looks like: no tx_index key at all."""
    r = wt(slot, i, venue="pumpswap")
    del r["tx_index"]
    return r


def no_tx_scenario() -> list[str]:
    """QP on #1165: the rows without tx_index PASS THROUGH (no key, receive-order form), are counted, and are neither dropped nor marked as loss."""
    bad = []
    ad = ta.ArenaTipAdapter()
    a, b = resolved_row(20, 1), resolved_row(20, 2)
    b["pool"], b["signature"] = a["pool"], "s20x9"  # two transactions of ONE pool in one slot, both without tx_index
    rows = ad.feed([("trade", wt(19)), ("trade", a), ("trade", b), ("trade", wt(21, 3, venue="pumpswap"))])
    if [r["type"] for r in rows] != ["trade"] * 4:
        return [f"row types {[r['type'] for r in rows]}"]
    if any("tx_index" in r for r in rows[1:3]) or rows[3].get("tx_index") != 3:
        bad.append("tx_index key present on a pass-through row, or lost on a numbered one")
    keys = [ArenaEngine.order_key(r, r["slot"], r["t_recv_ms"]) for r in rows[1:3]]
    if keys != [(20, 1, a["t_recv_ms"], a["signature"], 0), (20, 1, b["t_recv_ms"], "s20x9", 0)]:
        bad.append(f"receive-order keys {keys}")
    c = ad.counts()
    if not (c["rows_no_tx_index"] == 2 and c["pool_slots_no_tx_index"] == 1 and c["pool_slots_multi_sig"] == 1 and c.get("pool_slots_mixed", 0) == 0 and not c.get("gap_rows")):
        bad.append(f"counts {c}")
    return bad


def test_a_pumpswap_row_without_tx_index_passes_through_with_the_receive_order_key_and_is_counted():
    assert no_tx_scenario() == []


def test_the_engine_keeps_a_consistent_order_when_the_two_key_forms_meet_in_one_pool(tmp_path):
    ad = ta.ArenaTipAdapter()
    items = []
    for s in range(100, 112):
        row = wt(s, 0, venue="pumpswap", pool="PX", signature=f"z{s}", base_reserve=206 * 10**12 + s)
        row["pool"] = "PX"
        if s % 3 == 0:
            del row["tx_index"]  # whole (pool, slot) group without tx_index: no mixing inside a slot
        items.append(("trade", row))
        if s % 4 == 0:
            r2 = wt(s, 1, venue="pumpswap", signature=f"y{s}")
            r2["pool"] = "PX"
            if s % 3 == 0:
                del r2["tx_index"]
            items.append(("trade", r2))
    rows = ad.feed(items)
    eng = mk_engine(tmp_path)
    for r in rows:
        eng.on_row(r)
    h = eng.pools["PX"]
    assert h.keys == sorted(h.keys) and len(h.keys) == len(rows) and eng.counters.get("rows_dup", 0) == 0
    assert [k[0] for k in h.keys] == sorted(k[0] for k in h.keys)  # slot is the first element on both forms
    assert {k[1] for k in h.keys} == {0, 1} and ad.counts().get("pool_slots_mixed", 0) == 0


def test_a_mixed_pool_slot_group_is_a_pumpswap_data_loss_gap_before_the_row_that_revealed_it():
    ad = ta.ArenaTipAdapter()
    a, b = resolved_row(20, 1), wt(20, 2, venue="pumpswap")
    a["pool"] = b["pool"] = "PM"
    rows = ad.feed([("trade", a), ("trade", b)])
    assert [r["type"] for r in rows] == ["trade", "gap", "trade"] and rows[1]["prog"] == "pumpswap" and rows[1]["kind"] == "tip_gap_mixed_tx_index" and rows[1]["data_loss"] is True
    assert ad.counts()["pool_slots_mixed"] == 1 and ad.stats["gap_rows"] == 1


def test_a_bonding_row_without_tx_index_is_still_dropped_and_marked_with_its_own_prog():
    bonding = wt(21)
    del bonding["tx_index"]
    ad = ta.ArenaTipAdapter()
    rows = ad.feed([("trade", wt(20)), ("trade", bonding), ("trade", wt(22))])
    g = [r for r in rows if r["type"] == "gap"]
    assert [r["type"] for r in rows] == ["trade", "gap", "trade"] and g[0]["prog"] == "pumpfun" and g[0]["kind"] == "tip_gap_bad_row_tx_index" and ad.stats["rows_dropped_tx_index"] == 1


def test_gap_kinds_are_never_truncated_and_progs_follow_the_loss():
    assert ta.gap_kind("skip_contradicted_refetch") == "tip_gap_skip_contradicted_refetch" and len(ta.gap_kind("skip_contradicted_refetch")) > 32
    assert ta.gap_prog(tr.TipGap(1, 1, "unresolved_dropped", 1)) == "pumpswap" and ta.gap_prog(tr.TipGap(1, 9, "backlog_jump", 1)) == "all"
    bad_ps = wt(21, venue="pumpswap", side="hold")
    rows = ta.ArenaTipAdapter().feed([("trade", wt(20)), ("trade", bad_ps), ("trade", wt(22))])
    assert [r["prog"] for r in rows if r["type"] == "gap"] == ["pumpswap"]


def test_real_decoder_and_follower_rows_from_resolve_unresolved_pass_through(tmp_path):
    """The REAL rows_from_block + resolve_unresolved path inside the REAL TipFollower: a PumpSwap sell whose pool is not in the cache comes back through the account lookup."""
    from observe.trade_decode import WSOL_MINT
    from tools.test_fast_tip_follower import _block, _line

    blk = _block(5)
    blk["transactions"][0]["meta"]["logMessages"] = [_line("pumpswap_sell_event.b64")]
    blk["transactions"] += _block(5)["transactions"][:0]
    rpc = Rpc(tip=5, script={5: [blk]})
    f = _follower(tmp_path, rpc, clock=SetClock(T0), lookup=lambda pools: {pools[0]: ("Mint111", WSOL_MINT)})
    f.precreate(T0)
    _seed(f, 4)
    f.step()
    walker = [json.loads(x) for p in (tmp_path / "out").glob("trades-*.jsonl") for x in p.read_text().splitlines()]
    assert len(walker) == 1 and walker[0]["mint"] == "Mint111" and "tx_index" not in walker[0]  # the fact the QP measured on exploration data, from the real code
    healthy(tmp_path / "state", T0)
    src = ta.ArenaTipSource(tr.TipReader(tmp_path / "out", tmp_path / "state", start_ms=T0, catch_up_hours=0), tmp_path / "state", now_ms=lambda: T0, io_hook=lambda: True)
    src.start()
    rows = src.poll()
    assert [r["type"] for r in rows] == ["trade"] and "tx_index" not in rows[0] and rows[0]["prog"] == "pumpswap" and not src.adapter.stats["gap_rows"]
    assert ArenaEngine.order_key(rows[0], rows[0]["slot"], rows[0]["t_recv_ms"])[:2] == (5, 1) and src.adapter.counts()["rows_no_tx_index"] == 1


def test_the_vendored_reader_is_the_r4b_reader_byte_for_byte():
    """A test, not an arena process (the class of tools/test_arena_stack.py): it opens the source file to prove identity."""
    import hashlib

    root = Path(ta.__file__).parent
    vend = (root / "arena_tip_reader.py").read_text()
    marker = "# ---- end of the vendoring header; the rest of this file is tools/r4b_tip_reader.py byte for byte ----\n"
    head, body = vend.split(marker, 1)
    src_bytes = (root / "r4b_tip_reader.py").read_bytes()
    assert body.encode() == src_bytes
    blob = hashlib.sha1(b"blob %d\0" % len(src_bytes) + src_bytes).hexdigest()
    assert f"source blob   {blob}" in head, "re-vendor tools/arena_tip_reader.py from the new tools/r4b_tip_reader.py and re-review it"
    assert tr.PINNED_BLOBS == __import__("tools.fast_tip_follower", fromlist=["x"]).PINNED_DECODER_BLOBS
    for mod in ("arena_tip_adapter", "arena_tip_reader"):
        assert "from tools import r4b" not in (root / f"{mod}.py").read_text() and "import tools.r4b" not in (root / f"{mod}.py").read_text()  # no arena module imports an r4b module


def test_the_output_delta_against_the_feed_is_exactly_the_documented_one():
    """Real decoder records: the keys the feed writes (unslimmed record minus TRADE_DROP) against the keys the adapter writes from the real walker (slim) row."""
    from observe.trade_decode import records_from_logs
    from tools.pump_history_backfill import rows_from_block
    from tools.test_fast_tip_follower import _line

    drop = arena_feed_consts()["TRADE_DROP"]
    line = _line("pumpswap_sell_event.b64")
    feed_rec = records_from_logs([line], slot=5, signature="sg", t_recv_ms=1, commitment="confirmed", feed="x", pool_mints={}, event_v=True)[0]
    feed_keys = {k for k in feed_rec if k not in drop}
    blk = {"slot": 5, "blockTime": 1790318932, "parentSlot": 4, "transactions": [{"meta": {"err": None, "logMessages": [line]}, "transaction": {"signatures": ["sg"]}}]}
    pool_mints = {feed_rec["pool"]: ("Mint111", WSOL)}
    walker = rows_from_block(blk, pool_mints, "x", event_v=True)["trades"][0]
    out = ta.trade_row({**walker, "t_recv_ms": 1, "source": "tip", "tx_index": 0})
    tip_keys = set(out) - {"v", "type", "prog", "slot", "t_recv_ms", "signature", "event_index", "tx_index", "block_time"}
    assert feed_keys - tip_keys == ta.FEED_ONLY_KEYS["pumpswap"], sorted(feed_keys - tip_keys)  # exactly the documented loss ...
    assert tip_keys - feed_keys == set()  # ... and nothing the feed does not write
    # what the loss does to ArenaState: constant product on the base move instead of pool_quote_amount + lp_fee
    from tools.arena_state import pumpswap_post

    q, b = int(feed_rec["quote_reserve"]) + int(feed_rec["virtual_quote_reserves"]), int(feed_rec["base_reserve"])
    full = pumpswap_post(q, b, feed_rec["side"] == "buy", int(feed_rec["sol_lamports"]), int(feed_rec["token_raw"]), feed_rec.get("lp_fee"), feed_rec.get("pool_quote_amount"))
    fallback = pumpswap_post(q, b, feed_rec["side"] == "buy", int(feed_rec["sol_lamports"]), int(feed_rec["token_raw"]), None, None)
    assert full != fallback and feed_rec.get("lp_fee") is not None  # the state's price falls back: a documented difference, not hidden


@pytest.mark.parametrize("patch", [{"venue": "other"}, {"side": "hold"}, {"sol_lamports": "1"}, {"signature": ""}, {"slot": True}])
def test_other_unusable_rows_are_dropped_and_marked(patch):
    rows = ta.ArenaTipAdapter().feed([("trade", wt(10)), ("trade", {**wt(11), **patch}), ("trade", wt(12))])
    types = [r["type"] for r in rows]
    assert types.count("trade") == 2 and types.count("gap") == 1


# ------------------------------------------------------------------------------------------------------------------------------------ mutations of the adapter
@pytest.mark.parametrize("name", ["no_flush", "not_data_loss", "skip_is_gap", "gap_at_end", "tx_index_stripped", "no_tx_dropped", "no_tx_marked_gap"])
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
    elif name in ("no_tx_dropped", "no_tx_marked_gap"):
        orig_t = ta.trade_row

        def strict(w):
            if w.get("tx_index") is None and w.get("venue") == "pumpswap":
                raise ta.BadRow("tx_index")  # the 451bac48 behaviour: drop the row and mark a data_loss gap
            return orig_t(w)

        monkeypatch.setattr(ta, "trade_row", strict)
    if name == "skip_is_gap":
        assert skip_scenario(tmp_path) != []
    elif name == "tx_index_stripped":
        assert order_key_scenario() != []
    elif name in ("no_tx_dropped", "no_tx_marked_gap"):
        assert no_tx_scenario() != []
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
    f.process_slot(1)  # a commit writes checkpoint.json (no lag fields in it: the block lag is read from status.json)
    ck = json.loads(f.ckpt_path.read_text())
    assert {"last_done", "last_t_recv_ms", "gaps", "skipped", "writing"} <= set(ck) and not {"lag_ms", "block_lag_ms_p90"} & set(ck)


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
    assert ta.feed_delay_slots([{"block_lag_ms_p90": 2000}] * 100, 0.4, ingest_lag_ms=150)["d"] == 6  # (2000 + 150) / 400 = 5.375 -> 6
    with pytest.raises(ValueError):
        ta.feed_delay_slots(samples[:10], 0.4)
    with pytest.raises(ValueError):
        ta.feed_delay_slots(samples, 0.0)
