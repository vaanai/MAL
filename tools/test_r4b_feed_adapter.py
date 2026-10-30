"""Tests for tools/r4b_feed_adapter.py. SYNTHETIC dict rows only; no tape file, no network, no key.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_feed_adapter.py tools/test_r4b_live.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("duckdb")

from tools import r4b_feed_adapter as fa  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools.test_r4b_live import MARKS, drive, event_stream, scenario, sig  # noqa: E402

WSOL = rl.WSOL_MINT
T0 = 1_786_665_600


def ps(mint="MINT", pool="POOL", ts=T0, slot=100, signature="sig1", **kw):
    return {"v": 1, "type": "trade", "prog": "pumpswap", "slot": slot, "t_recv_ms": ts * 1000 + 5, "signature": signature, "event_index": 0,
            "venue": "pumpswap", "mint": mint, "mint_source": "create_pool" if mint else "unresolved", "trader": "TR", "side": "buy",
            "sol_lamports": 10**9, "token_raw": 10**6, "event_ts": ts, "pool": pool, "quote_reserve": 85 * 10**9, "base_reserve": 206 * 10**12,
            "quote_mint": WSOL if mint else None, "virtual_quote_reserves": 17_600_000_000, "ix_name": "buy", **kw}


def bd(mint="MINT", ts=T0, slot=90, signature="sig2", **kw):
    return {"v": 1, "type": "trade", "prog": "pumpfun", "slot": slot, "t_recv_ms": ts * 1000, "signature": signature, "event_index": 0,
            "venue": "pump_bonding", "mint": mint, "mint_source": "event", "trader": "TR2", "side": "sell", "sol_lamports": 5 * 10**8, "token_raw": 7,
            "event_ts": ts, "pool": None, "quote_reserve": 30 * 10**9, "base_reserve": 10**15, "quote_mint": WSOL, **kw}


def pool_create(pool="POOL", mint="MINT", ts=None, slot=95, signature="sigp"):
    r = {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": slot, "t_recv_ms": T0 * 1000, "signature": signature, "event_index": 0, "pool": pool,
         "mint": mint, "quote_mint": WSOL}
    if ts is not None:
        r["event_ts"] = ts
    return r


def kinds(ms):
    return [m.kind for m in ms]


# ------------------------------------------------------------------------------------------------------------------------------- mapping
def test_pumpswap_trade_maps_to_the_walker_schema():
    out = fa.FeedAdapter().feed(ps(ts=T0 + 3, slot=123, signature="abc"))
    assert kinds(out) == ["trade"]
    w = out[0].row
    assert set(w) == set(rl.TRADE_COLS) | {"quote_mint"}
    assert (w["block_time"], w["tx_index"], w["slot"], w["signature"], w["event_index"]) == (T0 + 3, None, 123, "abc", 0)
    assert (w["venue"], w["mint"], w["pool"], w["trader"], w["side"], w["quote_mint"]) == ("pumpswap", "MINT", "POOL", "TR", "buy", WSOL)
    assert (w["sol_lamports"], w["token_raw"], w["quote_reserve"], w["base_reserve"], w["virtual_quote_reserves"]) == (10**9, 10**6, 85 * 10**9, 206 * 10**12,
                                                                                                                       17_600_000_000)
    assert not out[0].halts_buys


def test_bonding_trade_maps_with_no_pool_and_no_v():
    w = fa.FeedAdapter().feed(bd(ts=T0 + 9))[0].row
    assert set(w) == set(rl.TRADE_COLS) | {"quote_mint"}
    assert (w["venue"], w["pool"], w["virtual_quote_reserves"], w["tx_index"], w["block_time"], w["side"]) == ("pump_bonding", None, None, None, T0 + 9, "sell")


def test_complete_pool_create_and_create_map_to_migration_and_create_rows():
    a = fa.FeedAdapter()
    c = a.feed({"v": 1, "type": "complete", "prog": "pumpfun", "slot": 91, "t_recv_ms": 1, "signature": "sc", "event_index": 0, "mint": "MINT",
                "trader": "x", "bonding_curve": "BC", "event_ts": T0 + 1, "quote_mint": WSOL})[0]
    assert c.kind == "migration" and set(c.row) == set(rl.MIG_COLS)
    assert (c.row["type"], c.row["mint"], c.row["slot"], c.row["block_time"], c.row["tx_index"], c.row["pool"]) == ("complete", "MINT", 91, T0 + 1, None, None)
    m = a.feed(pool_create(ts=T0 + 2))[0]
    assert m.kind == "migration" and set(m.row) == set(rl.MIG_COLS)
    assert (m.row["type"], m.row["pool"], m.row["quote_mint"], m.row["mint"], m.row["block_time"], m.row["tx_index"]) == ("migration", "POOL", WSOL, "MINT", T0 + 2, None)
    assert a.feed(pool_create(signature="s2"))[0].row["block_time"] is None  # pool_create has no event_ts in the schema: not guessed
    cr = a.feed({"v": 1, "type": "create", "prog": "pumpfun", "slot": 80, "t_recv_ms": 1, "signature": "sx", "event_index": 0, "mint": "MINT", "trader": "x",
                 "creator": "CRE", "event_ts": T0 - 900, "is_mayhem_mode": True})[0]
    assert cr.kind == "create" and set(cr.row) == set(rl.CREATE_COLS)
    assert (cr.row["type"], cr.row["creator"], cr.row["is_mayhem_mode"], cr.row["block_time"], cr.row["slot"]) == ("create", "CRE", True, T0 - 900, 80)
    no_mm = a.feed({"type": "create", "slot": 80, "signature": "sy", "mint": "M2", "creator": "C", "event_ts": T0})[0]
    assert no_mm.row["is_mayhem_mode"] is None


def test_gap_rows_become_gap_records_that_halt_buys():
    a = fa.FeedAdapter()
    g = a.feed({"type": "gap", "prog": "pumpswap", "slot": 7, "t_recv_ms": 5_000, "signature": None, "kind": "slot_jump", "data_loss": True, "from_ms": 1000,
                "to_ms": 4000, "missed_slots": 40})[0]
    assert g.kind == "gap" and isinstance(g.row, fa.GapRecord) and g.halts_buys
    assert (g.row.kind, g.row.instant_ms, g.row.from_ms, g.row.to_ms, g.row.prog, g.row.data_loss) == ("slot_jump", 4000, 1000, 4000, "pumpswap", True)
    op = a.feed({"type": "gap", "prog": "all", "kind": "silence", "data_loss": True, "closed": False, "from_ms": 10, "to_ms": 30_000, "t_recv_ms": 30_000})[0]
    assert op.row.closed is False and op.halts_buys
    covered = a.feed({"type": "gap", "prog": "pumpfun", "kind": "socket_reconnect", "data_loss": False, "from_ms": 1, "to_ms": 2, "t_recv_ms": 3})[0]
    assert covered.kind == "gap" and not covered.halts_buys
    unknown = a.feed({"type": "gap", "prog": "pumpfun", "kind": "disk_reserve", "t_recv_ms": 9})[0]  # data_loss missing: fail closed
    assert unknown.halts_buys and unknown.row.instant_ms == 9 and unknown.row.data_loss is None
    assert a.feed({"type": "gap", "data_loss": True})[0].kind == "reject"  # no kind


def test_hb_start_stop_are_ignored_and_counted_and_unknown_types_are_rejected():
    a = fa.FeedAdapter()
    for t in ("hb", "start", "stop"):
        assert a.feed({"type": t, "prog": "all", "slot": 0}) == []
    assert a.stats["rows_hb"] == a.stats["rows_start"] == a.stats["rows_stop"] == 1
    r = a.feed({"type": "mystery", "signature": "q"})[0]
    assert r.kind == "reject" and r.row.reason == "unknown_type" and r.halts_buys and a.stats["reject_unknown_type"] == 1


@pytest.mark.parametrize("patch,reason", [({"side": "Buy"}, "bad_side"), ({"side": None}, "bad_side"), ({"event_ts": None}, "bad_event_ts"),
                                          ({"event_ts": 1.5e9}, "bad_event_ts"), ({"sol_lamports": 1.5}, "bad_sol_lamports"),
                                          ({"slot": True}, "bad_slot"), ({"token_raw": "7"}, "bad_token_raw"), ({"quote_reserve": None}, "bad_quote_reserve"),
                                          ({"virtual_quote_reserves": None}, "bad_virtual_quote_reserves"), ({"pool": None}, "bad_pool"),
                                          ({"trader": ""}, "bad_trader"), ({"signature": None}, "bad_signature"), ({"venue": "raydium"}, "bad_venue"),
                                          ({"prog": "pumpfun"}, "bad_venue"), ({"event_index": "0"}, "bad_event_index")])
def test_bad_pumpswap_trade_fields_are_rejected_not_defaulted(patch, reason):
    out = fa.FeedAdapter().feed({**ps(), **patch})
    assert kinds(out) == ["reject"] and out[0].row.reason == reason and out[0].halts_buys


def test_bonding_trade_needs_a_mint_and_complete_needs_its_fields():
    a = fa.FeedAdapter()
    assert a.feed(bd(mint=None))[0].row.reason == "bad_mint"
    assert a.feed({"type": "complete", "slot": 1, "mint": "M"})[0].row.reason == "bad_event_ts"
    assert a.feed({"type": "pool_create", "slot": 1, "pool": "P", "mint": "M"})[0].row.reason == "bad_quote_mint"
    assert a.feed({"type": "create", "slot": 1, "event_ts": 5})[0].row.reason == "bad_mint"


# ------------------------------------------------------------------------------------------------------------------------------- unresolved pools
def test_unresolved_rows_are_buffered_then_emitted_in_arrival_order_after_the_pool_create():
    a = fa.FeedAdapter()
    assert a.feed(ps(mint=None, ts=T0, signature="u1", slot=101)) == [] and a.feed(ps(mint=None, ts=T0 + 1, signature="u2", slot=100)) == []
    assert a.feed(ps(mint=None, pool="OTHER", ts=T0 + 1, signature="o1")) == []
    out = a.feed(pool_create(ts=T0 + 2))
    assert kinds(out) == ["migration", "trade", "trade"]
    assert [m.row["signature"] for m in out[1:]] == ["u1", "u2"]  # arrival order, not slot order
    assert all(m.row["mint"] == "MINT" and m.row["quote_mint"] == WSOL and m.row["pool"] == "POOL" for m in out[1:])
    assert list(a.unresolved) == ["OTHER"] and a.stats["unresolved_resolved"] == 2
    out = a.feed(ps(mint=None, ts=T0 + 3, signature="u3"))  # a later null-mint row of a known pool resolves at once
    assert kinds(out) == ["trade"] and out[0].row["mint"] == "MINT"


def test_pool_info_resolves_without_making_a_migration_row():
    a = fa.FeedAdapter()
    assert a.feed(ps(mint=None, signature="u1")) == []
    out = a.feed({"type": "pool_info", "prog": "pumpswap", "slot": 0, "t_recv_ms": 1, "signature": None, "event_index": None, "pool": "POOL",
                  "mint": "MINT", "quote_mint": WSOL, "source": "getMultipleAccounts"})
    assert kinds(out) == ["trade"] and out[0].row["mint"] == "MINT"
    assert a.feed({"type": "pool_info", "pool": "P2"})[0].row.reason == "bad_pool_info"


def test_an_unresolved_row_is_never_guessed_it_expires_and_is_reported():
    a = fa.FeedAdapter(pending_s=900)
    a.feed(ps(mint=None, ts=T0, signature="old1"))
    a.feed(ps(mint=None, ts=T0 + 5, signature="old2"))
    out = a.feed(bd(ts=T0 + 1000, signature="clock"))  # the newest event_ts moved 1000 s: the sweep drops both
    assert kinds(out) == ["reject", "trade"]
    r = out[0].row
    assert (r.reason, r.n, r.type, r.halts_buys) == ("unresolved_expired", 2, "trade", False) and not out[0].halts_buys
    assert a.unresolved == {} and a.stats["unresolved_expired"] == 2 and a.stats["reject_unresolved_expired"] == 2
    assert kinds(a.feed(pool_create(ts=T0 + 1001))) == ["migration"]  # a pool_create that comes too late finds nothing to flush


def test_unresolved_buffer_is_bounded_per_pool_and_in_pool_count():
    a = fa.FeedAdapter(max_rows_per_pool=2, max_pools=2)
    for i in range(2):
        assert a.feed(ps(mint=None, signature=f"a{i}")) == []
    over = a.feed(ps(mint=None, signature="a2"))
    assert kinds(over) == ["reject"] and over[0].row.reason == "unresolved_overflow" and not over[0].halts_buys
    assert a.feed(ps(mint=None, pool="P2", signature="b0")) == []
    over = a.feed(ps(mint=None, pool="P3", signature="c0"))
    assert over[0].row.reason == "unresolved_overflow" and sorted(a.unresolved) == ["P2", "POOL"]


def test_a_pool_that_names_two_mints_is_a_conflict_and_the_first_stands():
    a = fa.FeedAdapter()
    a.feed(pool_create(mint="MINT"))
    out = a.feed(pool_create(mint="OTHER", signature="s2"))
    assert kinds(out) == ["reject"] and out[0].row.reason == "pool_mint_conflict" and out[0].halts_buys
    assert a.pools["POOL"] == ("MINT", WSOL)
    out = a.feed(ps(mint="OTHER", signature="s3"))  # a row that names another mint for a known pool
    assert kinds(out) == ["reject"] and out[0].row.reason == "pool_mint_conflict"
    assert kinds(a.feed(ps(mint="MINT", signature="s4"))) == ["trade"]


def test_row_mint_and_quote_mint_are_never_overwritten_by_the_resolver():
    a = fa.FeedAdapter()
    a.feed(pool_create())
    w = a.feed(ps(mint="MINT", quote_mint="SOME_OTHER_QUOTE"))[0].row
    assert (w["mint"], w["quote_mint"]) == ("MINT", "SOME_OTHER_QUOTE")  # passed through, not filtered (disclosed in the module docstring)


# ------------------------------------------------------------------------------------------------------------------------------- end to end
def _to_tape(kind: str, r: dict) -> dict:
    base = {"v": 1, "slot": r["slot"], "t_recv_ms": int(r.get("block_time") or T0) * 1000 + 7, "signature": r.get("signature") or f"x{id(r)}",
            "event_index": r.get("event_index")}
    if kind == "trade":
        t = {**base, "type": "trade", "prog": "pumpswap" if r["venue"] == "pumpswap" else "pumpfun", "venue": r["venue"], "mint": r["mint"],
             "trader": r["trader"], "side": r["side"], "sol_lamports": r["sol_lamports"], "token_raw": r["token_raw"], "event_ts": r["block_time"],
             "pool": r["pool"], "quote_reserve": r["quote_reserve"], "base_reserve": r["base_reserve"]}
        if r["venue"] == "pumpswap":
            t |= {"virtual_quote_reserves": r["virtual_quote_reserves"], "quote_mint": WSOL}
            if r["mint"] != "XNOM":  # XNOM has no pool_create in the scenario: its rows keep their mint (a null-mint row of it would never reach the engine)
                t["mint"] = None  # every other PumpSwap row arrives unresolved; the pool_create of its pool names the mint
        return t
    if kind == "migration" and r["type"] == "complete":
        return {**base, "type": "complete", "prog": "pumpfun", "mint": r["mint"], "event_ts": r["block_time"]}
    if kind == "migration":
        return {**base, "type": "pool_create", "prog": "pumpswap", "pool": r["pool"], "mint": r["mint"], "quote_mint": r["quote_mint"]}
    return {**base, "type": "create", "prog": "pumpfun", "mint": r["mint"], "creator": r["creator"], "event_ts": r["block_time"],
            "is_mayhem_mode": r["is_mayhem_mode"]}


def test_adapted_tape_rows_decide_like_the_same_walker_rows_with_no_tx_index():
    """The synthetic scenario as tape rows (PumpSwap mints null, resolved by pool_create) through the adapter into the engine, against the walker rows
    with tx_index nulled fed directly: every mark's rows, features, scores, ranks and actions are equal."""
    hours = scenario()
    ev = event_stream(hours)
    base_ev = [(t, o, n, k, {**r, "tx_index": None} if k != "create" else r) for t, o, n, k, r in ev]
    ad = fa.FeedAdapter()
    adapted = []
    for t, o, n, k, r in ev:
        for m in ad.feed(_to_tape(k, r)):
            assert m.kind in ("trade", "migration", "create") and not m.halts_buys, m
            adapted.append((t, o, n, m.kind, m.row))
    # the pool_create precedes every print of its pool in the stream, so nothing needed buffering here; the lookup path filled the mint
    assert len(adapted) == len(ev) and not ad.unresolved and ad.stats["unresolved_buffered"] == 0
    assert all(m[4]["mint"] is not None for m in adapted if m[3] == "trade")
    _, want = drive(base_ev, MARKS)
    _, got = drive(adapted, MARKS)
    assert sum(len(r.mints) for r in want) > 40 and any(r.actions for r in want)
    assert [sig(r) for r in got] == [sig(r) for r in want]


def test_no_send_path_or_key_in_the_adapter():
    import inspect

    src = inspect.getsource(fa).split('"""', 2)[2]
    for word in ("probe_live", "LiveExecutor", "Keypair", "sendTransaction", "send_transaction", "private_key", "helius", "requests", "urllib", "socket"):
        assert word not in src, word
